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

The pure core (:func:`select_comparable_runs`, :func:`build_trend`) is separated
from the Azure boundary (:func:`load_published_completeness`,
:func:`snapshot_from_manifest`, :func:`select_history`), so the tests use an
in-memory reader plus temporary Parquet files and never touch the network
(plan section 8).
"""
from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    """Chronological completeness series of one field (oldest -> newest)."""

    field: str
    required: bool
    values: tuple[float, ...]
    direction: str
    delta: float


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
    """

    runs_used: int
    comparable: bool
    note: str | None
    sources: dict[str, SourceTrend]


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
    """Build the chronological series and last step of one field."""
    values = tuple(
        run.completeness_by_source[source_id].fields[field].completeness_pct
        for run in runs
    )
    delta = values[-1] - values[-2]
    required = runs[-1].completeness_by_source[source_id].fields[field].required
    return FieldTrend(
        field=field,
        required=required,
        values=values,
        direction=_direction(delta),
        delta=delta,
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
    downloaded to a temporary location and read as Parquet; unreadable objects
    are skipped. For a Multi-site portal the rows are filtered first by its
    ``site`` column (``sources.get_source(source_id).site``). The readable
    objects are concatenated, deduplicated and measured (RF-6, RF-7).

    Returns ``None`` when no object yielded usable offers for the source (all
    objects unreadable, no ``site`` column, or no matching rows): there is no
    published population to measure. A :class:`landing.RemoteError` propagates
    unchanged so the caller reports the trend as not checked (RF-8, RF-13).

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
            local = _local_path(base_dir, index, entry.remote)
            index += 1
            reader.download(_full_remote_key(manifest, entry.remote), local)
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

    Thin helper over the pure core: it lists the scraper's manifests
    (``landing.list_manifest_keys``), loads each one
    (``landing.load_manifest``) and builds a :class:`RunSnapshot` for it. The
    manifest whose stamp is ``label`` is used as the ``current`` anchor (with
    ``current_fingerprint``); when it is not present, a fingerprint-only anchor
    is used. Then :func:`select_comparable_runs` keeps only the runs whose
    fingerprint matches and caps the series at ``limit``. Manifests without a
    fingerprint are excluded (RF-7).
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
    return select_comparable_runs(current, candidates, limit=limit)
