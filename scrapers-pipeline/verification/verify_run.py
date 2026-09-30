"""Thin CLI that coordinates the daily-run diagnostic (T-40; RF-1, RF-11–RF-14).

This module is the entry point of the verification: it selects the latest
finished run (:mod:`verification.run_evidence`), measures the obtained data of
each of the nine independent sources (:mod:`verification.completeness`),
classifies them (:mod:`verification.status`), optionally checks the landing and
the trend through a read-only remote reader (:mod:`verification.landing`,
:mod:`verification.trends`) and builds the Spanish report
(:mod:`verification.report`).

Hard rules (RF-12, NFR, constitution #3/#5):

- It **never executes scrapers**, starts processes or calls the supervisor.
- It **never writes** data, configuration or publication. Its only output is
  text on stdout.
- It never modifies workspace files: the local reads are read-only and the
  Azure temporaries (when a reader is given) are owned and cleaned by
  :class:`verification.landing.AzCopyReader`.

The business logic lives in the other ``verification`` modules; this file only
orchestrates inputs and outputs. Identifiers/comments are in English, the
report and messages to the person are in Spanish (constitution #6).
"""
from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from verification import (
    completeness,
    fingerprint,
    investigation,
    landing,
    publication,
    report,
    run_evidence,
    sources,
    status,
    trends,
)

# A local Parquet reader maps a path to a PyArrow table. Tests inject one to
# avoid touching the disk; by default PyArrow reads the file (a dependency
# already justified in the plan, section 6.3).
ObtainedReader = Callable[[str], pa.Table]

# One scraper publishes one manifest covering its sources (RF-2, RF-8).
_SCRAPERS: tuple[str, ...] = ("indeed", "linkedin", "infojobs", "multi_site")

# Spanish reasons for an unanalysable run (RF-1, RF-13).
_INCONCLUSIVE_IN_PROGRESS_ES = (
    "la ejecución más reciente todavía no ha terminado; no se puede verificar "
    "el proceso"
)
_INCONCLUSIVE_NO_RUN_ES = (
    "no se encontró ninguna ejecución completada en los logs"
)

# The web check is always person-assisted: the CLI only records that it is
# pending, it never navigates or asserts a cause (RF-9, RF-11, RF-12).
_UNCONFIRMED_DETAIL = (
    "pendiente de comprobar en la web real (asistido por persona)"
)


@dataclass(frozen=True)
class _SourceMeasurement:
    """Obtained data of one source, ready for status/report/investigation."""

    source: str
    table: pa.Table | None
    structural_failure: bool
    structural_error: str | None
    completeness: completeness.SourceCompleteness | None
    offers: tuple[dict, ...]
    searches: tuple[fingerprint.SearchDimension, ...]


@dataclass(frozen=True)
class _RemoteState:
    """Remote manifests per scraper plus the scrapers that could not be read.

    ``unavailable`` holds the scrapers whose manifest listing raised
    :class:`verification.landing.RemoteError` (connectivity/credentials), so the
    publication is reported as *not checked* instead of as wrong (RF-8, RF-13).
    """

    manifests: dict[str, landing.Manifest | None]
    unavailable: frozenset[str]


# --------------------------------------------------------------------------
# Source helpers
# --------------------------------------------------------------------------


def _scraper_for(source_id: str) -> str:
    """Return the scraper whose manifest covers ``source_id`` (RF-2)."""
    return "multi_site" if source_id in sources.MULTI_SITE_SITES else source_id


def _inconclusive_reason_es(reason: str | None) -> str:
    """Translate the run-evidence inconclusive reason into Spanish (RF-13)."""
    if reason == run_evidence.INCONCLUSIVE_RUN_IN_PROGRESS:
        return _INCONCLUSIVE_IN_PROGRESS_ES
    return _INCONCLUSIVE_NO_RUN_ES


# --------------------------------------------------------------------------
# Obtained snapshot resolution and measurement
# --------------------------------------------------------------------------


def _snapshot_sort_key(path: Path) -> tuple[str, float]:
    """Sort snapshots by stamped name first, modification time as tie-break."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return (path.name, modified)


def _resolve_snapshot(projects_root: Path, pattern: str) -> Path | None:
    """Resolve a snapshot path/glob to the most recent existing file.

    ``sources`` stores a glob for Indeed/InfoJobs and an exact path for
    LinkedIn/Multi-site. Stamped names sort chronologically, so they win; the
    modification time only breaks ties. Returns ``None`` when nothing matches.
    """
    root = Path(projects_root)
    if any(char in pattern for char in "*?["):
        matches = sorted(root.glob(pattern))
        if not matches:
            return None
        return max(matches, key=_snapshot_sort_key)
    candidate = root / pattern
    return candidate if candidate.is_file() else None


def _default_obtained_reader(path: str) -> pa.Table:
    """Read a local Parquet file with PyArrow (the production path)."""
    return pq.read_table(path)


def _read_obtained_table(
    path: Path, obtained_reader: ObtainedReader | None
) -> pa.Table:
    """Read ``path`` through the injected reader or PyArrow by default."""
    reader = (
        obtained_reader if obtained_reader is not None else _default_obtained_reader
    )
    return reader(str(path))


def _obtained_structure(
    source_id: str, path: Path, obtained_reader: ObtainedReader | None
) -> tuple[pa.Table | None, completeness.ObtainedParquet]:
    """Read a Parquet file and describe its structure without raising.

    Mirrors ``completeness.read_source_parquet`` but through the injectable
    reader, so tests can simulate an unreadable or incomplete file without
    touching the disk. A bad file is data (a structural failure), never a crash.
    """
    try:
        table = _read_obtained_table(path, obtained_reader)
    except Exception as exc:  # noqa: BLE001 - a bad file is data, not a crash
        return None, completeness.ObtainedParquet(
            source=source_id,
            path=str(path),
            readable=False,
            rows=None,
            columns=(),
            missing_columns=(),
            error=repr(exc),
        )
    columns = tuple(table.schema.names)
    missing = tuple(
        column
        for column in sources.required_columns(source_id)
        if column not in columns
    )
    return table, completeness.ObtainedParquet(
        source=source_id,
        path=str(path),
        readable=True,
        rows=table.num_rows,
        columns=columns,
        missing_columns=missing,
        error=None,
    )


def _filter_site(table: pa.Table, site: str) -> pa.Table:
    """Return only the rows of ``table`` whose ``site`` matches (Multi-site)."""
    column = table.column("site")
    mask = pc.equal(column, pa.scalar(site, type=column.type))
    return table.filter(mask)


def _measure_source(
    projects_root: Path,
    source_id: str,
    evidence: run_evidence.RunEvidence,
    obtained_reader: ObtainedReader | None,
) -> _SourceMeasurement:
    """Locate and measure the obtained snapshot of one source (RF-3, RF-4).

    A stale snapshot (``RunEvidence.snapshot_stale``) is never used as the
    run's data. A missing, unreadable or structurally incomplete file is a
    structural failure and yields no completeness; otherwise the unique offers
    are deduplicated and measured, and the search dimensions are read for the
    investigation context.
    """
    source = sources.get_source(source_id)
    empty = _SourceMeasurement(source_id, None, False, None, None, (), ())
    if evidence.snapshot_stale:
        return empty
    path = _resolve_snapshot(projects_root, source.evidence["snapshot"])
    if path is None:
        return empty

    table, structure = _obtained_structure(source_id, path, obtained_reader)
    if table is None or completeness.is_structural_failure(structure):
        error = structure.error
        if error is None and structure.missing_columns:
            error = "missing columns: " + ", ".join(structure.missing_columns)
        return _SourceMeasurement(source_id, None, True, error, None, (), ())

    # A Multi-site portal shares the unified table: only its own rows count.
    if source.site is not None and table.num_rows > 0 and "site" in table.schema.names:
        table = _filter_site(table, source.site)

    measured = completeness.measure_completeness(table, source_id)
    offers = completeness.unique_offers(table, source_id)
    searches = fingerprint.search_dimensions_from_table(
        table, source_id, site=source.site
    )
    return _SourceMeasurement(
        source_id, table, False, None, measured, offers, searches
    )


# --------------------------------------------------------------------------
# Remote publication and trend
# --------------------------------------------------------------------------


def _manifest_stamp(key: str) -> str:
    """Return the stamp encoded in a manifest key (``<scraper>/<stamp>.json``)."""
    return PurePosixPath(key).stem


# Only these two exact stamp layouts are accepted. ``strptime`` alone would
# back-fill partial numeric stamps as a wrong time (``20260930_0611`` ->
# 06:01:01), so the shape is validated before parsing (T-55).
_MANIFEST_STAMP_FORMATS: tuple[tuple[str, str], ...] = (
    (r"^\d{8}_\d{6}$", "%Y%m%d_%H%M%S"),
    (r"^\d{8}T\d{6}$", "%Y%m%dT%H%M%S"),
)


def _parse_manifest_stamp(stamp: str) -> datetime | None:
    """Parse a manifest stamp, or ``None`` when it does not carry a date.

    Real stamps are ``yyyyMMdd_HHmmss`` (written by the upload wrapper); the
    test fixtures and some historical manifests use the ISO-like
    ``yyyyMMddTHHmmss`` form. Only those two exact shapes are accepted, so a
    partial or over-long numeric stamp (``20260930_0611``, ``20260930_061``,
    ``20260930_06112``) has no usable date and can never be an anchor (T-55).
    """
    for pattern, format_ in _MANIFEST_STAMP_FORMATS:
        if re.fullmatch(pattern, stamp):
            try:
                return datetime.strptime(stamp, format_)
            except ValueError:
                return None
    return None


def _run_window(
    summary: run_evidence.PipelineSummary | None,
) -> tuple[datetime, datetime] | None:
    """Return the local ``[start, end]`` window of the analysed run.

    ``None`` when the summary or either timestamp is missing/unparseable: an
    unknown window never selects a manifest as the run anchor (T-55, RF-1).
    """
    if summary is None or not summary.started_at or not summary.finished_at:
        return None
    try:
        start = datetime.fromisoformat(summary.started_at)
        end = datetime.fromisoformat(summary.finished_at)
    except ValueError:
        return None
    return (start, end)


def _stamp_within_window(
    stamp: str, window: tuple[datetime, datetime] | None
) -> bool:
    """Return True when ``stamp`` is parseable and falls in ``window``."""
    if window is None:
        return False
    moment = _parse_manifest_stamp(stamp)
    if moment is None:
        return False
    start, end = window
    return start <= moment <= end


def _stamp_moment(key: str) -> datetime:
    """Return the parsed stamp of a manifest key already known to be valid."""
    moment = _parse_manifest_stamp(_manifest_stamp(key))
    if moment is None:  # pragma: no cover - candidates are filtered first
        return datetime.min
    return moment


def _latest_manifest(
    reader: landing.RemoteReader,
    scraper: str,
    window: tuple[datetime, datetime] | None,
) -> landing.Manifest | None:
    """Load the run's latest manifest of ``scraper``, or ``None``.

    Only manifests whose stamp falls inside the analysed run's window are
    candidates: a manifest from another execution must never be presented as
    this run's publication nor anchor a trend (T-55, RF-1, RF-8). A stamp
    without a usable date is not a candidate either.
    """
    keys = landing.list_manifest_keys(reader, scraper)
    candidates = [
        key for key in keys if _stamp_within_window(_manifest_stamp(key), window)
    ]
    if not candidates:
        return None
    latest = max(candidates, key=_stamp_moment)
    return landing.load_manifest(reader, latest, scraper=scraper)


def _collect_remote_state(
    reader: landing.RemoteReader | None,
    window: tuple[datetime, datetime] | None,
) -> _RemoteState:
    """Load one in-window manifest per scraper, tolerating connectivity failures.

    Without a reader nothing is loaded. A scraper whose listing fails is kept
    in ``unavailable`` so its publication is reported as not checked instead of
    as pending or wrong (RF-8, RF-13). A scraper with no manifest inside the
    run window gets ``None``: there is no publication anchor for this run
    (T-55).
    """
    if reader is None:
        return _RemoteState({}, frozenset())
    manifests: dict[str, landing.Manifest | None] = {}
    unavailable: set[str] = set()
    for scraper in _SCRAPERS:
        try:
            manifests[scraper] = _latest_manifest(reader, scraper, window)
        except landing.RemoteError:
            manifests[scraper] = None
            unavailable.add(scraper)
    return _RemoteState(manifests, frozenset(unavailable))


def _prepared_data(
    evidence: run_evidence.RunEvidence, measured: _SourceMeasurement
) -> bool:
    """Return True when the run left data that should have been published.

    Two signals count (T-55, RF-8): the run's own captured-offer counter above
    zero, and a locally measured snapshot with offers. Everything else (a
    null/zero counter and no measured offers) means the source prepared
    nothing, so the absence of a manifest is "nothing to publish" and not a
    pending upload.
    """
    if evidence.offers_current_run is not None and evidence.offers_current_run > 0:
        return True
    if measured.completeness is not None and measured.completeness.total_offers > 0:
        return True
    return False


def _publication_for(
    reader: landing.RemoteReader | None,
    remote: _RemoteState,
    source_id: str,
    measured: completeness.SourceCompleteness | None,
    *,
    had_prepared_data: bool,
) -> publication.SourcePublication:
    """Build the publication stage of one source (RF-6, RF-8).

    The obtained stage is always attached so the report can show counts. The
    remote check only runs with a reader: a manifest is verified and its
    published delta measured; a connectivity/credential failure marks the
    publication as *not checked* instead of failing the source (RF-8, RF-13).
    Without a reader the publication is simply not checked.

    When the run left no manifest for the scraper, ``had_prepared_data``
    decides between *pending* (data was prepared but the object is not visible
    yet) and *not applicable* (nothing was prepared to publish, so its absence
    is no longer reported as a pending upload; T-55, RF-8).
    """
    scraper = _scraper_for(source_id)
    if reader is None or scraper in remote.unavailable:
        return publication.build_source_publication(
            source=source_id, obtained=measured, published=None, not_checked=True
        )
    manifest = remote.manifests.get(scraper)
    if manifest is None:
        if not had_prepared_data:
            # The run prepared nothing for this source: there is nothing to
            # publish, so the absent manifest is not a pending upload.
            return publication.build_source_publication(
                source=source_id,
                obtained=measured,
                published=None,
                manifest_state=None,
                not_applicable=True,
            )
        # Data was prepared but no manifest of this run is visible: the object
        # is not there yet, never an error.
        return publication.build_source_publication(
            source=source_id, obtained=measured, published=None, manifest_state=None
        )
    try:
        check = landing.verify_manifest(manifest, reader)
    except landing.RemoteError:
        return publication.build_source_publication(
            source=source_id, obtained=measured, published=None, not_checked=True
        )
    # Only ``STATE_PENDING`` is short-circuited: the object does not exist yet,
    # so measuring the published delta would download an absent object, raise
    # ``RemoteError`` and degrade the known pending state to "not checked"
    # (RF-8). Every other state (ok, rejected, mismatch, unverified) keeps
    # measuring the delta as before.
    if check.state == landing.STATE_PENDING:
        return publication.build_source_publication(
            source=source_id,
            obtained=measured,
            published=None,
            manifest_state=landing.STATE_PENDING,
        )
    try:
        published = trends.load_published_completeness(manifest, reader, source_id)
    except landing.RemoteError:
        return publication.build_source_publication(
            source=source_id, obtained=measured, published=None, not_checked=True
        )
    return publication.build_source_publication(
        source=source_id,
        obtained=measured,
        published=published,
        manifest_state=check.state,
        rejected=check.rejected,
    )


def _trend_read_failure_note(scraper: str, error: landing.RemoteError) -> str:
    """Return the Spanish note of a trend that could not be read (T-58).

    The ``RemoteError`` message is already sanitized (no SAS token), so it is
    safe to include as the observed cause (RF-7, RF-13).
    """
    return (
        f"no se pudo calcular la tendencia de {scraper}: no se pudieron leer "
        f"los objetos publicados ({error})"
    )


def _build_trends(
    reader: landing.RemoteReader | None, remote: _RemoteState
) -> tuple[dict[str, trends.SourceTrend], str | None, int]:
    """Compute the per-source trends and the global note/runs used (RF-7).

    Without a reader there is no comparable history: no trend and the Spanish
    "no history" note. Otherwise each scraper's in-window manifest is used as
    the current anchor and its up-to-five comparable runs are traced; a source
    without a usable series simply has no ``SourceTrend``.

    A scraper whose manifests/objects cannot be read (``RemoteError``) is
    never silenced (T-58): it contributes a Spanish failure note with its
    sanitized cause, so a read failure is not presented as "no comparable
    history". The generic :data:`trends.NO_HISTORY_NOTE` is only used when
    there is neither a history note nor a failure note. Successful series are
    still returned alongside the failure notes (RF-7, RF-13).
    """
    if reader is None:
        return {}, trends.NO_HISTORY_NOTE, 0
    by_source: dict[str, trends.SourceTrend] = {}
    notes: list[str] = []
    runs_used = 0
    for scraper, manifest in remote.manifests.items():
        if manifest is None:
            continue
        scope = sources.fingerprint_scope(scraper)
        try:
            runs, summary = trends.select_history_summary(
                reader,
                current_fingerprint=manifest.fingerprint,
                scraper=scraper,
                label=manifest.stamp,
                sources_scope=scope,
            )
        except landing.RemoteError as error:
            notes.append(_trend_read_failure_note(scraper, error))
            continue
        result = trends.build_trend(runs)
        for source_id, source_trend in result.sources.items():
            by_source[source_id] = source_trend
        if summary.note:
            notes.append(summary.note)
        runs_used = max(runs_used, result.runs_used)
    if not by_source:
        note = " ".join(dict.fromkeys(notes)) if notes else trends.NO_HISTORY_NOTE
        return {}, note, 0
    note = " ".join(dict.fromkeys(notes)) if notes else None
    return by_source, note, runs_used


def _pipeline_evidence(
    summary: run_evidence.PipelineSummary | None, source_id: str
) -> tuple[str, ...]:
    """Return the general-pipeline status line of a source, if any."""
    if summary is None:
        return ()
    key = "multi_site" if source_id in sources.MULTI_SITE_SITES else source_id
    pipeline_status = summary.statuses.get(key)
    if pipeline_status is None:
        return ()
    return (
        "estado del pipeline: "
        f"{pipeline_status.status} "
        f"(subidos={pipeline_status.uploaded}, rechazados={pipeline_status.rejected})",
    )


# --------------------------------------------------------------------------
# Diagnostic orchestration
# --------------------------------------------------------------------------


def run_diagnostic(
    projects_root: Path | str | None = None,
    logs_dir: Path | str | None = None,
    *,
    reader: landing.RemoteReader | None = None,
    obtained_reader: ObtainedReader | None = None,
) -> report.DiagnosticReport:
    """Analyse the latest finished run and build the diagnostic report (RF-1).

    Read-only: it never executes scrapers, writes data or changes
    configuration/publication (RF-12). ``reader`` enables the landing checks
    and the trend; without it neither is attempted and the publication is
    reported as not checked. ``obtained_reader`` injects the local Parquet
    reading for tests; by default PyArrow reads the files.

    A run that cannot be analysed (still in progress, or no finished run at
    all) yields an inconclusive report with a Spanish reason and no invented
    sources (RF-13).
    """
    diagnostic = run_evidence.build_run_diagnostic(projects_root, logs_dir)
    if not diagnostic.analyzable:
        global_status = status.classify_global(
            {},
            analyzable=False,
            inconclusive_reason=_inconclusive_reason_es(
                diagnostic.inconclusive_reason
            ),
        )
        return report.build_report(
            sources=(),
            global_status=global_status,
            trend_note=None,
            runs_used=0,
        )

    root = (
        Path(projects_root)
        if projects_root is not None
        else run_evidence.DEFAULT_PROJECTS_ROOT
    )
    window = _run_window(diagnostic.summary)
    remote = _collect_remote_state(reader, window)
    trends_by_source, trend_note, runs_used = _build_trends(reader, remote)

    statuses: dict[str, status.SourceStatus] = {}
    source_reports: list[report.SourceReport] = []
    investigations: list[investigation.InvestigationOutcome] = []
    for source_id in sources.source_ids():
        evidence = diagnostic.sources[source_id]
        measured = _measure_source(root, source_id, evidence, obtained_reader)
        source_status = status.classify_source(
            source_id,
            measured.completeness,
            evidence_available=(
                evidence.outcome != run_evidence.OUTCOME_NO_EVIDENCE
            ),
            structural_failure=measured.structural_failure,
            structural_error=measured.structural_error,
        )
        statuses[source_id] = source_status
        evidence_lines = _pipeline_evidence(diagnostic.summary, source_id)
        for context in investigation.build_contexts(
            source_id,
            source_status,
            completeness=measured.completeness,
            offers=measured.offers,
            searches=measured.searches,
            evidence=evidence_lines,
            run=evidence,
        ):
            investigations.append(
                investigation.unconfirmed_web(
                    context,
                    detail=_UNCONFIRMED_DETAIL,
                    recommendation=investigation.suggest_recommendation(context),
                    manual_check=investigation.suggest_manual_check(context),
                )
            )
        source_reports.append(
            report.build_source_report(
                source_id,
                status=source_status,
                run=evidence,
                publication=_publication_for(
                    reader,
                    remote,
                    source_id,
                    measured.completeness,
                    had_prepared_data=_prepared_data(evidence, measured),
                ),
                trend=trends_by_source.get(source_id),
                progress=evidence.offers_current_run,
                stop_reason=None,
                evidence=evidence_lines,
            )
        )

    global_status = status.classify_global(statuses, analyzable=True)
    return report.build_report(
        sources=source_reports,
        global_status=global_status,
        trend_note=trend_note,
        runs_used=runs_used,
        investigations=investigations,
    )


def _build_reader() -> landing.AzCopyReader | None:
    """Build the read-only landing reader from the environment, or ``None``.

    A missing storage account (or any failure to build the adapter) means the
    diagnostic continues offline; it never breaks the run (RF-13). The SAS is
    read from the environment and never logged.
    """
    try:
        base_url = landing.base_url_from_env()
    except landing.RemoteError:
        return None
    sas = os.environ.get(landing.SAS_TOKEN_ENV, "")
    return landing.AzCopyReader(base_url, sas)


def main(argv: list[str] | None = None) -> int:
    """Run the diagnostic CLI, print the report and return 0 (RF-14).

    With ``--offline`` Azure is never contacted. Otherwise a landing reader is
    built from the environment; if that fails the run continues offline with a
    warning. The only output is the Spanish report on stdout.
    """
    parser = argparse.ArgumentParser(
        prog="verify_run",
        description="Diagnóstico de la última ejecución diaria de scrapers.",
    )
    parser.add_argument("--projects-root", default=None)
    parser.add_argument("--logs-dir", default=None)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="no consultar la landing de Azure",
    )
    args = parser.parse_args(argv)

    reader = None
    if not args.offline:
        reader = _build_reader()
        if reader is None:
            print(
                "Aviso: no se pudo configurar el acceso a la landing; se "
                "continúa en modo offline."
            )
    try:
        diagnostic_report = run_diagnostic(
            args.projects_root, args.logs_dir, reader=reader
        )
    finally:
        if reader is not None:
            reader.close()

    print(report.render_report(diagnostic_report), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
