"""Completeness trend across comparable runs (T-33; RF-7).

The trend is the variation of the *published* completeness per source and
field over the last comparable runs kept in Azure (plan section 4.3). It never
uses the local snapshot and never creates a local history store
(constitution #5).

Two runs are comparable only when their sources+searches fingerprint matches
(:func:`verification.fingerprint.fingerprints_match`). A manifest without a
fingerprint is **not** comparable by inference and does not contribute to the
series (RF-7, plan decision 6). The fingerprint is derived from the published
delta, so it can be stricter than the raw scraper configuration; that is a
deliberate conservative choice: it may drop a genuinely comparable run, but it
never mixes two different configurations into one series.

The pure core (:func:`select_comparable_runs`, :func:`build_trend`,
:func:`summarize_history`, :func:`build_trend_from_candidates`) is separated
from the Azure boundary (:func:`load_published_completeness`,
:func:`snapshot_from_manifest`, :func:`select_history`,
:func:`select_history_summary`), so the tests use an in-memory reader plus
temporary Parquet files and never touch the network (plan section 8).

T-34 makes the drops explicit: :func:`summarize_history` counts the candidates
rejected for having no fingerprint or a different one and explains it in
Spanish, so an insufficient or mismatched history is never presented as a
comparable series (RF-7, RF-13).
"""
from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from verification import completeness, field_contract, fingerprint, landing, sources

# Up to five comparable runs are shown, including the analysed one (RF-7).
DEFAULT_HISTORY_LIMIT = 5

# Per-field direction of the last step of the series. Plain strings so the
# report layer (T-38) can render them without another enum import.
DIRECTION_IMPROVED = "improved"
DIRECTION_REGRESSED = "regressed"
DIRECTION_STABLE = "stable"

# Message shown when there is no reference to trace a trend (RF-7, RF-13).
NO_HISTORY_NOTE = (
    "no hay ejecuciones comparables suficientes para mostrar tendencia"
)


@dataclass(frozen=True)
class RunSnapshot:
    """Published completeness of one run, plus its comparability anchor.

    ``label`` is the run's date/stamp (``Manifest.stamp``) and must sort
    lexicographically in chronological order (ISO date or timestamp).
    ``fingerprint`` is the manifest's sources+searches fingerprint, or ``None``
    for old manifests that do not carry one (never comparable by inference).
    """

    label: str
    fingerprint: dict | None
    completeness_by_source: Mapping[str, completeness.SourceCompleteness]


@dataclass(frozen=True)
class FieldTrend:
    """Chronological completeness series of one field (oldest -> newest).

    ``counts`` holds the ``(valid, total)`` pair of every run, in the same
    order and length as ``values``, so the report can show every percentage
    with its counts (NFR). It defaults to ``()`` to keep older constructions
    (5 positional arguments) working.
    """

    field: str
    required: bool
    values: tuple[float, ...]
    direction: str
    delta: float
    counts: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class SourceTrend:
    """Trend of one source over the runs actually used."""

    source: str
    runs_used: int
    fields: dict[str, FieldTrend]


@dataclass(frozen=True)
class TrendResult:
    """Trend of a whole diagnostic.

    ``comparable`` is True only when at least two runs could be compared, i.e.
    a series can be traced at all. When it is False, ``note`` explains it in
    Spanish and ``sources`` is empty.

    The extra exclusion counters (T-34) are filled by
    :func:`build_trend_from_candidates`; they keep their defaults when
    :func:`build_trend` is used directly, so existing construction stays
    backward compatible.
    """

    runs_used: int
    comparable: bool
    note: str | None
    sources: dict[str, SourceTrend]
    considered: int = 0
    excluded_without_fingerprint: int = 0
    excluded_different_fingerprint: int = 0


@dataclass(frozen=True)
class HistorySummary:
    """Why a history could (not) be compared, and what was dropped (T-34).

    ``considered`` counts the analysed run plus every candidate examined.
    ``runs_used`` is how many runs the default series would actually use
    (``current`` plus comparable candidates, capped by
    :data:`DEFAULT_HISTORY_LIMIT`). ``comparable`` is True only with at least
    two usable runs. ``note`` is Spanish and, when applicable, states that
    there is not enough history, that the configuration changed or that some
    manifests carry no fingerprint.
    """

    runs_used: int
    considered: int
    excluded_without_fingerprint: int
    excluded_different_fingerprint: int
    comparable: bool
    note: str | None


# --- Pure core ---------------------------------------------------------------


def _label_key(snapshot: RunSnapshot) -> str:
    """Return the sorting key of a run label (its trimmed stamp)."""
    return str(snapshot.label).strip()


def select_comparable_runs(
    current: RunSnapshot,
    candidates: Sequence[RunSnapshot],
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> tuple[RunSnapshot, ...]:
    """Return ``current`` plus its comparable candidates, oldest -> newest.

    A candidate is comparable only when :func:`fingerprints_match` accepts its
    fingerprint against ``current``'s. A missing fingerprint on either side is
    never comparable, so old manifests without a fingerprint are excluded
    (RF-7). The result is sorted chronologically by ``label`` and capped at
    ``limit`` runs; the analysed ``current`` is always kept, even in the
    (defensive) case where it is not the newest label.
    """
    if limit < 1:
        return (current,)

    matching: list[RunSnapshot] = [current]
    for candidate in candidates:
        if candidate is current:
            continue
        if fingerprint.fingerprints_match(current.fingerprint, candidate.fingerprint):
            matching.append(candidate)

    matching.sort(key=_label_key)
    if len(matching) > limit:
        kept = matching[-limit:]
        if not any(run is current for run in kept):
            # Never drop the run being analysed: keep it plus the newest
            # candidates that still fit.
            tail = matching[-(limit - 1) :] if limit > 1 else []
            kept = sorted([current, *tail], key=_label_key)
        matching = kept
    return tuple(matching)


def _shared_sources(runs: Sequence[RunSnapshot]) -> tuple[str, ...]:
    """Return the source ids present in *every* run, alphabetically.

    A source missing from any run is dropped: mixing it would trace a false
    series (its completeness would compare against a run that did not publish
    it at all).
    """
    if not runs:
        return ()
    common: set[str] | None = None
    for run in runs:
        ids = set(run.completeness_by_source)
        common = ids if common is None else common & ids
        if not common:
            return ()
    return tuple(sorted(common or ()))


def _ordered_fields(shared: set[str]) -> list[str]:
    """Order shared fields by the data contract, then by any extra field."""
    order = [field for field in field_contract.MEASURED_FIELDS if field in shared]
    order.extend(sorted(shared - set(order)))
    return order


def _source_fields(
    runs: Sequence[RunSnapshot], source_id: str
) -> tuple[str, ...]:
    """Return the fields measured in every run for ``source_id``, ordered."""
    shared: set[str] | None = None
    for run in runs:
        measured = run.completeness_by_source.get(source_id)
        fields = set(measured.fields) if measured is not None else set()
        shared = fields if shared is None else shared & fields
        if not shared:
            return ()
    return tuple(_ordered_fields(shared or set()))


def _direction(delta: float) -> str:
    """Map the last step of the series to a direction constant."""
    if delta > 0.0:
        return DIRECTION_IMPROVED
    if delta < 0.0:
        return DIRECTION_REGRESSED
    return DIRECTION_STABLE


def _field_trend(
    runs: Sequence[RunSnapshot], source_id: str, field: str
) -> FieldTrend:
    """Build the chronological series, counts and last step of one field."""
    stats = [
        run.completeness_by_source[source_id].fields[field] for run in runs
    ]
    values = tuple(stat.completeness_pct for stat in stats)
    counts = tuple((stat.valid, stat.total) for stat in stats)
    delta = values[-1] - values[-2]
    required = stats[-1].required
    return FieldTrend(
        field=field,
        required=required,
        values=values,
        direction=_direction(delta),
        delta=delta,
        counts=counts,
    )


def build_trend(runs: Sequence[RunSnapshot]) -> TrendResult:
    """Build the per-source/per-field completeness trend of ``runs`` (RF-7).

    ``runs`` must be chronological (oldest -> newest), as produced by
    :func:`select_comparable_runs`. With fewer than two runs there is no
    reference to trace: the result is not comparable, carries a Spanish note
    and has no series. Otherwise, only the sources present in *all* runs and
    the fields measured in *all* runs are traced, so no false series is
    invented for a source or field that appeared or disappeared mid-history.
    """
    runs_used = len(runs)
    if runs_used < 2:
        return TrendResult(
            runs_used=runs_used,
            comparable=False,
            note=NO_HISTORY_NOTE,
            sources={},
        )

    trends: dict[str, SourceTrend] = {}
    for source_id in _shared_sources(runs):
        fields = {
            field: _field_trend(runs, source_id, field)
            for field in _source_fields(runs, source_id)
        }
        trends[source_id] = SourceTrend(
            source=source_id, runs_used=runs_used, fields=fields
        )
    return TrendResult(
        runs_used=runs_used,
        comparable=True,
        note=None,
        sources=trends,
    )


def _has_fingerprint(fp: dict | None) -> bool:
    """Return True when ``fp`` carries a usable comparability hash."""
    return isinstance(fp, Mapping) and bool(fp.get("hash"))


def _runs_phrase(count: int) -> str:
    """Return a Spanish phrase for ``count`` previous runs."""
    return "1 ejecución anterior" if count == 1 else f"{count} ejecuciones anteriores"


def summarize_history(
    current: RunSnapshot,
    candidates: Sequence[RunSnapshot],
) -> HistorySummary:
    """Summarise how many runs are usable and why the rest were dropped (RF-7).

    Pure and side-effect free. ``considered`` counts the analysed ``current``
    plus every candidate (the ``current`` object is never counted twice). A
    candidate is dropped as ``excluded_without_fingerprint`` when it has no
    usable fingerprint (old manifests, never comparable by inference) and as
    ``excluded_different_fingerprint`` when its fingerprint does not match
    ``current``'s (the configuration changed). ``runs_used`` is the number of
    runs the default series would actually use (``current`` plus comparable
    candidates, capped by :data:`DEFAULT_HISTORY_LIMIT`).

    ``note`` is Spanish and reflects, when applicable: too little comparable
    history, a configuration change on some candidates, and candidates without
    a fingerprint. ``comparable`` is True only with at least two usable runs.
    """
    considered = 1
    without = 0
    different = 0
    for candidate in candidates:
        if candidate is current:
            continue
        considered += 1
        if not _has_fingerprint(candidate.fingerprint):
            without += 1
        elif not fingerprint.fingerprints_match(
            current.fingerprint, candidate.fingerprint
        ):
            different += 1

    runs_used = len(select_comparable_runs(current, candidates))
    parts: list[str] = []
    if runs_used < 2:
        parts.append(NO_HISTORY_NOTE)
    if different:
        parts.append(
            f"la configuración cambió en {_runs_phrase(different)}; "
            "no se comparan esas partes"
        )
    if without:
        parts.append(
            f"{_runs_phrase(without)} sin huella de fuentes/búsquedas no se "
            "consideran comparables"
        )
    note = ". ".join(parts) if parts else None
    return HistorySummary(
        runs_used=runs_used,
        considered=considered,
        excluded_without_fingerprint=without,
        excluded_different_fingerprint=different,
        comparable=runs_used >= 2,
        note=note,
    )


def _combine_notes(primary: str | None, secondary: str | None) -> str | None:
    """Combine two Spanish notes without losing or duplicating text."""
    if not primary:
        return secondary or None
    if not secondary:
        return primary
    if primary in secondary:
        return secondary
    if secondary in primary:
        return primary
    return f"{primary} {secondary}"


def build_trend_from_candidates(
    current: RunSnapshot,
    candidates: Sequence[RunSnapshot],
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> TrendResult:
    """Build the trend of ``current`` against its comparable candidates (RF-7).

    Selects up to ``limit`` comparable runs (:func:`select_comparable_runs`),
    builds the series with :func:`build_trend` and annotates the result with
    the exclusion counters and the Spanish note from :func:`summarize_history`.
    The insufficient-history note from :func:`build_trend` is preserved. Runs
    dropped for a missing or different fingerprint never enter the series.
    """
    selected = select_comparable_runs(current, candidates, limit=limit)
    result = build_trend(selected)
    summary = summarize_history(current, candidates)
    return replace(
        result,
        considered=summary.considered,
        excluded_without_fingerprint=summary.excluded_without_fingerprint,
        excluded_different_fingerprint=summary.excluded_different_fingerprint,
        note=_combine_notes(result.note, summary.note),
    )


# --- Azure boundary ----------------------------------------------------------


def _full_remote_key(manifest: landing.Manifest, remote: str) -> str:
    """Return the container-relative key of one manifest entry."""
    key = remote.lstrip("/")
    return f"{manifest.scraper}/{key}".strip("/") if manifest.scraper else key


def _local_path(base_dir: Path, index: int, remote: str) -> Path:
    """Return a safe local path for one downloaded object.

    Only the file name is reused, so a corrupt ``remote`` key can never write
    outside ``base_dir`` (no traversal, no absolute/drive paths).
    """
    name = PurePosixPath(remote.replace("\\", "/")).name or f"object_{index}"
    return base_dir / f"{index:04d}_{name}"


def _filter_by_site(table: pa.Table, site: str) -> pa.Table | None:
    """Return only the rows of a Multi-site table whose ``site`` matches.

    Returns ``None`` when the table carries no ``site`` column: those rows
    cannot be attributed to a portal, so the caller skips the object.
    """
    if "site" not in table.schema.names:
        return None
    column = table.column("site")
    mask = pc.equal(column, pa.scalar(site, type=column.type))
    return table.filter(mask)


def _concat_tables(tables: Sequence[pa.Table]) -> pa.Table:
    """Concatenate readable tables, tolerating schema differences."""
    if len(tables) == 1:
        return tables[0]
    try:
        return pa.concat_tables(tables, promote_options="permissive")
    except TypeError:  # older PyArrow without promote_options
        try:
            return pa.concat_tables(tables)
        except pa.ArrowInvalid:
            return pa.Table.from_pylist(
                [row for table in tables for row in table.to_pylist()]
            )


def load_published_completeness(
    manifest: landing.Manifest,
    reader: landing.RemoteReader,
    source_id: str,
    *,
    workdir: str | Path | None = None,
) -> completeness.SourceCompleteness | None:
    """Measure the published completeness of one source from its manifest.

    Every manifest entry that is not ``bad`` and carries a ``remote`` key is
    resolved against the landing (exact key, or a unique file name under the
    published directory when AzCopy added a staging folder), downloaded to a
    temporary location and read as Parquet; unresolvable and unreadable
    objects are skipped. For a Multi-site portal the rows are filtered first
    by its ``site`` column (``sources.get_source(source_id).site``). The
    readable objects are concatenated, deduplicated and measured (RF-6,
    RF-7).

    Returns ``None`` when no object yielded usable offers for the source (none
    resolvable, all unreadable, no ``site`` column, or no matching rows):
    there is no published population to measure. A :class:`landing.RemoteError`
    propagates unchanged so the caller reports the trend as not checked
    (RF-8, RF-13).

    Temporary downloads under a private directory are removed before returning;
    a caller-provided ``workdir`` is used as-is and left untouched.
    """
    portal_site = sources.get_source(source_id).site

    own_dir: tempfile.TemporaryDirectory | None = None
    if workdir is not None:
        base_dir = Path(workdir)
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        own_dir = tempfile.TemporaryDirectory(prefix="landing-trend-")
        base_dir = Path(own_dir.name)

    try:
        tables: list[pa.Table] = []
        index = 0
        for entry in manifest.files:
            if entry.status == "bad" or not entry.remote:
                continue
            resolved = landing.resolve_published_key(
                reader, _full_remote_key(manifest, entry.remote)
            )
            if resolved is None:
                # Not visible (or ambiguous): skip it, exactly like an
                # unreadable object. The real cause is reported by the caller
                # (RF-8, RF-13; T-58).
                continue
            local = _local_path(base_dir, index, entry.remote)
            index += 1
            reader.download(resolved, local)
            try:
                table = pq.read_table(str(local))
            except Exception:  # noqa: BLE001 - an unreadable object is skipped
                continue
            if portal_site is not None:
                table = _filter_by_site(table, portal_site)
                if table is None:
                    continue
            if table.num_rows == 0:
                continue
            tables.append(table)

        if not tables:
            return None
        combined = _concat_tables(tables)
        return completeness.measure_completeness(combined, source_id)
    finally:
        if own_dir is not None:
            own_dir.cleanup()


def snapshot_from_manifest(
    manifest: landing.Manifest,
    reader: landing.RemoteReader,
    *,
    sources_scope: Sequence[str],
    label: str,
    workdir: str | Path | None = None,
) -> RunSnapshot:
    """Build a :class:`RunSnapshot` from a manifest's published objects.

    Uses ``manifest.fingerprint`` as the comparability anchor and measures the
    published completeness of every source in ``sources_scope``. Sources
    without usable published data are omitted, so a portal that did not
    publish anything that day cannot inject a false zero into a series.
    """
    measured: dict[str, completeness.SourceCompleteness] = {}
    for source_id in sources_scope:
        result = load_published_completeness(
            manifest, reader, source_id, workdir=workdir
        )
        if result is not None:
            measured[source_id] = result
    return RunSnapshot(
        label=label,
        fingerprint=manifest.fingerprint,
        completeness_by_source=measured,
    )


def _collect_history(
    reader: landing.RemoteReader,
    *,
    current_fingerprint: dict | None,
    scraper: str,
    label: str,
    sources_scope: Sequence[str],
) -> tuple[RunSnapshot, list[RunSnapshot]]:
    """Return the analysed anchor and the other published runs of ``scraper``.

    Shared by :func:`select_history` and :func:`select_history_summary`: it
    lists the scraper's manifests (``landing.list_manifest_keys``), loads each
    one (``landing.load_manifest``) and builds a :class:`RunSnapshot` for it.
    The manifest whose stamp is ``label`` becomes the ``current`` anchor (with
    ``current_fingerprint``); when it is absent, a fingerprint-only anchor is
    used.
    """
    keys = landing.list_manifest_keys(reader, scraper)
    current: RunSnapshot | None = None
    candidates: list[RunSnapshot] = []
    for key in keys:
        manifest = landing.load_manifest(reader, key, scraper=scraper)
        if manifest is None:
            continue
        snapshot = snapshot_from_manifest(
            manifest,
            reader,
            sources_scope=sources_scope,
            label=manifest.stamp,
        )
        if manifest.stamp == label:
            current = RunSnapshot(
                label=label,
                fingerprint=current_fingerprint,
                completeness_by_source=snapshot.completeness_by_source,
            )
        else:
            candidates.append(snapshot)

    if current is None:
        current = RunSnapshot(
            label=label,
            fingerprint=current_fingerprint,
            completeness_by_source={},
        )
    return current, candidates


def select_history(
    reader: landing.RemoteReader,
    *,
    current_fingerprint: dict | None,
    scraper: str,
    label: str,
    sources_scope: Sequence[str],
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> tuple[RunSnapshot, ...]:
    """Return the comparable published runs of ``scraper``, oldest -> newest.

    Thin helper over the pure core: it collects the scraper's manifests and
    then :func:`select_comparable_runs` keeps only the runs whose fingerprint
    matches and caps the series at ``limit``. Manifests without a fingerprint
    are excluded (RF-7). Use :func:`select_history_summary` when the caller
    also needs the exclusions explained.
    """
    current, candidates = _collect_history(
        reader,
        current_fingerprint=current_fingerprint,
        scraper=scraper,
        label=label,
        sources_scope=sources_scope,
    )
    return select_comparable_runs(current, candidates, limit=limit)


def select_history_summary(
    reader: landing.RemoteReader,
    *,
    current_fingerprint: dict | None,
    scraper: str,
    label: str,
    sources_scope: Sequence[str],
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> tuple[tuple[RunSnapshot, ...], HistorySummary]:
    """Return the comparable runs *and* their :class:`HistorySummary` (T-34).

    Same selection as :func:`select_history`; additionally reports, in Spanish,
    how many runs were considered and how many were dropped for having no
    fingerprint or a different one. This is the entry point the report layer
    should use to avoid presenting an insufficient or mismatched history as a
    real comparable series (RF-7, RF-13).
    """
    current, candidates = _collect_history(
        reader,
        current_fingerprint=current_fingerprint,
        scraper=scraper,
        label=label,
        sources_scope=sources_scope,
    )
    runs = select_comparable_runs(current, candidates, limit=limit)
    summary = summarize_history(current, candidates)
    if summary.runs_used != len(runs) or summary.comparable != (len(runs) >= 2):
        # A non-default ``limit`` changes how many runs are actually used.
        comparable = len(runs) >= 2
        note = summary.note
        if not comparable and (note is None or NO_HISTORY_NOTE not in note):
            note = NO_HISTORY_NOTE if note is None else f"{NO_HISTORY_NOTE}. {note}"
        summary = replace(
            summary, runs_used=len(runs), comparable=comparable, note=note
        )
    return runs, summary
