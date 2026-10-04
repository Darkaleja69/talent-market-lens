"""Read, validate and freshness-check the daily diagnostic (T-01; RF-1, RF-13).

The diagnostic written by the 001 process is the input of every repair: this
module loads ``diagnostic_last.json``, validates its contract
(``schema_version: 1``) and warns, without failing, when it is older than the
last ``upload-*.log`` of the logs directory (a symptom of a newer run that has
not been diagnosed yet; plan section 2.1).

The loaded payload is kept available so the following tasks can extract the
failed sources and the secondary investigations from it (T-02, T-03).
:func:`select_targets` turns the payload into :class:`RepairTarget` objects for
every source with ``status: failed``, whatever its ``id`` (generic playbook),
each with its state, offers, reasons, diagnostic evidence and local evidence
paths. :func:`select_secondary_targets` turns the completeness investigations
(``required_field_below_target`` and ``optional_field_at_or_below_threshold``)
into secondary targets with their field and current percentage, and
:func:`select_all_targets` returns the prioritized queue: primary targets
first, secondary ones after (RF-1). Every target carries a per-field
:class:`QualityProfile` (T-04; RF-1, RF-7, RF-16) built from the data contract
and the diagnostic: required fields and the optionals the source publishes aim
at 100 %, an unpublished optional gets no target. An ``inconclusive`` global
status is rejected with a clear Spanish message because no repair must start
from it.

Design rules:

- A missing, unreadable or invalid diagnostic is a domain error
  (:class:`DiagnosticError`) with a clear Spanish message, never a crash with a
  raw ``OSError``/``JSONDecodeError``. The same error reports an inconclusive
  diagnostic, which cannot be selected from.
- An unsupported ``schema_version`` stops the process instead of interpreting
  the data wrongly (plan section 4).
- The freshness check never fails: a stale diagnostic yields a Spanish warning
  in :attr:`LoadedDiagnostic.warnings`, which callers can inspect.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from verification import field_contract, sources

# Only the schema this process knows how to interpret is accepted (plan §4).
SCHEMA_VERSION = 1

# scrapers-pipeline/repair/targets.py -> parents[1] is `scrapers-pipeline`.
DEFAULT_DIAGNOSTIC_PATH = (
    Path(__file__).resolve().parents[1] / "logs" / "diagnostic_last.json"
)

# Machine codes of the diagnostic contract used by the selection (plan §4):
# `failed` marks a repair target; `inconclusive` forbids starting any repair.
# `unknown` is only a local fallback for a secondary investigation whose
# source is absent from `sources[]`; it never comes from the diagnostic.
SOURCE_FAILED = "failed"
SOURCE_UNKNOWN = "unknown"
GLOBAL_INCONCLUSIVE = "inconclusive"

# Roles of the repair queue (RF-1): failed sources are primary targets, the
# completeness investigations are secondary ones, always prioritized after.
ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"

# Completeness investigation triggers that become secondary objectives (RF-1;
# plan §4). `source_failed` is intentionally absent: a failed source is
# already covered by its primary target, so it must not be duplicated.
TRIGGER_REQUIRED_FIELD = "required_field_below_target"
TRIGGER_OPTIONAL_FIELD = "optional_field_at_or_below_threshold"
SECONDARY_TRIGGERS = frozenset({TRIGGER_REQUIRED_FIELD, TRIGGER_OPTIONAL_FIELD})

# Local evidence paths of the source playbooks, relative to the repository
# root (plan §2.3). They are pointers for the web inspector: the wildcard
# entries are resolved by the operator against the local `output/`/`data/`
# folders, which are not versioned. Any source absent from this table is a new
# or unexpected failure and is served by the generic playbook (plan §6.7),
# which only carries the paths found in the diagnostic itself: no invented
# paths.
PLAYBOOK_EVIDENCE_PATHS: dict[str, tuple[str, ...]] = {
    "indeed": (
        "indeed_jobs_scraper/output/scrape_metadata_20261003_0006.json",
        "indeed_jobs_scraper/output/scrape_metadata_20261003_1247.json",
        "indeed_jobs_scraper/output/nightly_stderr_IE_attempt1.log",
        "indeed_jobs_scraper/output/log_20261003_*.log",
    ),
    "linkedin": (
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "linkedin_jobs_scraper/data/output/jobs.parquet",
        "linkedin_jobs_scraper/data/run.log",
    ),
    "infojobs": (
        "infojobs_jobs_scraper/data/run_nightly.log",
        "infojobs_jobs_scraper/data/nightly_stdout_attempt1.log",
        "infojobs_jobs_scraper/data/logs/run_20261003_*.log",
    ),
    "irishjobs": (
        "multi_site_job_scraper/data/merged/last_run.json",
        "multi_site_job_scraper/data/irishjobs/run.log",
    ),
    "glassdoor": (
        "multi_site_job_scraper/data/merged/last_run.json",
        "multi_site_job_scraper/data/glassdoor/run.log",
    ),
}

# A fresh run leaves an `upload-YYYY-MM-DD.log` or `upload-<source>-YYYY-MM-DD.log`
# in the logs directory; a name without a usable date is ignored.
_UPLOAD_LOG_RE = re.compile(r"^upload-(?:.+?-)?(?P<date>\d{4}-\d{2}-\d{2})\.log$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MISSING = object()


class DiagnosticError(RuntimeError):
    """The diagnostic could not be loaded, cannot be used or breaks its contract."""


@dataclass(frozen=True)
class LoadedDiagnostic:
    """A validated diagnostic plus the freshness warnings detected on load.

    ``payload`` is the parsed JSON exactly as written by the 001 process
    (``schema_version: 1``), ready for target extraction. ``warnings`` holds
    Spanish, code-inspectable notes (for example a stale diagnostic); loading
    never fails because of them.
    """

    path: Path
    payload: dict
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class QualityProfile:
    """Per-field quality profile of a repair target (T-04; RF-1, RF-7, RF-16).

    One entry per canonical measured field, in
    ``field_contract.MEASURED_FIELDS`` order. ``required`` comes from the data
    contract. ``current_pct`` is the diagnostic measurement of that field for
    the target source (``None`` when the field was not measured: never
    invented). ``target_pct`` is the goal: 100.0 for a required field and for
    an optional field the source publishes (``sources.field_aliases``
    non-empty; plan §2.5, §9), and ``None`` for an optional field the portal
    does not publish (for example ``skills`` in Indeed), whose absence is not
    a failure (RF-16). ``is_focus`` marks the field of the secondary
    investigation, so T-05/T-06 can highlight it; primary targets keep it
    ``False``.
    """

    field: str
    required: bool
    current_pct: float | None
    target_pct: float | None
    is_focus: bool = False


@dataclass(frozen=True)
class RepairTarget:
    """One failed source to repair, extracted from the diagnostic (T-02; RF-1).

    ``source`` is the diagnostic ``id`` (no fixed list: any source can produce
    a target), ``kind`` its ``direct``/``multi_site`` group and ``status`` its
    machine state (``failed`` for a primary target). ``outcome`` is the run
    result: ``blocked`` means CAPTCHA/anti-bot and ``error`` a technical
    failure or a non-zero exit; in Multi-site, ``exit=75`` is the progress
    watchdog and is only carried in ``evidence``, never reinterpreted here
    (plan §4).

    ``offers_current_run``/``offers_snapshot`` are the run counter and the
    aggregate snapshot counter (``None`` when the diagnostic has no data),
    ``failures`` the Spanish reasons and ``evidence`` the diagnostic's own
    evidence lines. ``evidence_paths`` are the local paths to inspect: the
    playbook paths of §2.3 for known sources plus the loaded diagnostic and,
    when present, its ``run.log_path``; a source absent from the playbook gets
    no invented path (plan §6.7). ``run_date`` is the date of the source run.

    ``role`` tells a primary target (a failed source) from a secondary one (a
    completeness investigation, plan §6.6). Secondary targets fill ``trigger``
    (``required_field_below_target``/``optional_field_at_or_below_threshold``),
    ``field`` (the affected canonical field) and ``current_pct`` (the field
    percentage taken from the diagnostic ``completeness``, or ``None`` when it
    is absent or not numeric). Primary targets keep those three at ``None``.

    ``quality`` is the per-field :class:`QualityProfile` of the target, in
    canonical field order and built from the diagnostic; it defaults to an
    empty tuple so direct constructions stay valid.
    """

    source: str
    kind: str | None
    status: str
    outcome: str | None
    offers_current_run: int | None
    offers_snapshot: int | None
    failures: tuple[str, ...]
    evidence: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    run_date: date | None
    field: str | None = None
    trigger: str | None = None
    current_pct: float | None = None
    role: str = ROLE_PRIMARY
    quality: tuple[QualityProfile, ...] = ()


def load_diagnostic(
    path: str | Path | None = None,
    *,
    logs_dir: str | Path | None = None,
) -> LoadedDiagnostic:
    """Load and validate the diagnostic, with its freshness warnings.

    ``path`` defaults to ``scrapers-pipeline/logs/diagnostic_last.json``,
    anchored to the package location, so it works from the repository root and
    from ``scrapers-pipeline/`` alike. ``logs_dir`` is the directory scanned for
    ``upload-*.log`` files; it defaults to the diagnostic's own directory.

    Raises :class:`DiagnosticError` (Spanish message with the path and the
    reason) when the file is missing, unreadable, is not a JSON object or does
    not declare the expected ``schema_version``. A diagnostic older than the
    last upload log only produces a warning, never an error.
    """
    diagnostic_path = Path(path) if path is not None else DEFAULT_DIAGNOSTIC_PATH
    payload = _read_payload(diagnostic_path)
    _validate_schema_version(payload, diagnostic_path)
    scan_dir = Path(logs_dir) if logs_dir is not None else diagnostic_path.parent
    return LoadedDiagnostic(
        path=diagnostic_path,
        payload=payload,
        warnings=_freshness_warnings(payload, scan_dir),
    )


def select_targets(loaded: LoadedDiagnostic) -> tuple[RepairTarget, ...]:
    """Extract the primary repair targets from a loaded diagnostic (RF-1).

    Every source with ``status: failed`` becomes a :class:`RepairTarget`, in
    the diagnostic order and whatever its ``id``: a source without a playbook
    still produces a generic target (plan §6.7). A correct diagnostic with no
    failed sources simply yields an empty tuple.

    Raises :class:`DiagnosticError` (Spanish message with the path and the
    reason) when the global status is ``inconclusive``: no repair must start
    from a diagnostic that could not be analysed.
    """
    payload = loaded.payload
    if _global_status(payload) == GLOBAL_INCONCLUSIVE:
        raise DiagnosticError(_inconclusive_message(loaded))
    run_date = _run_date(payload)
    selected: list[RepairTarget] = []
    for entry in _source_entries(payload):
        target = _target_from_source(entry, loaded, run_date)
        if target is not None:
            selected.append(target)
    return tuple(selected)


def select_secondary_targets(loaded: LoadedDiagnostic) -> tuple[RepairTarget, ...]:
    """Extract the secondary completeness targets from a diagnostic (RF-1).

    The ``investigations[]`` with trigger ``required_field_below_target`` or
    ``optional_field_at_or_below_threshold`` become secondary targets, in the
    diagnostic order. ``source_failed`` investigations are excluded: the
    failed source already has its primary target, and a secondary never
    replaces or duplicates it. A source may be both primary and secondary
    (for example Indeed or LinkedIn, which have field investigations).

    Each secondary carries its ``field``, ``trigger``, ``current_pct`` (the
    matching ``completeness[].pct`` of its source, or ``None`` when it is
    absent or not numeric) and the same source context as a primary target:
    state, offers, reasons, evidence and local evidence paths. An
    investigation without a usable field is ignored: a completeness objective
    needs a field.

    Raises :class:`DiagnosticError` under the same conditions as
    :func:`select_targets` (inconclusive global status).
    """
    payload = loaded.payload
    if _global_status(payload) == GLOBAL_INCONCLUSIVE:
        raise DiagnosticError(_inconclusive_message(loaded))
    run_date = _run_date(payload)
    selected: list[RepairTarget] = []
    for entry in _investigation_entries(payload):
        target = _secondary_target_from_investigation(entry, loaded, run_date)
        if target is not None:
            selected.append(target)
    return tuple(selected)


def select_all_targets(loaded: LoadedDiagnostic) -> tuple[RepairTarget, ...]:
    """Return the prioritized repair queue of a diagnostic (RF-1).

    Primary targets (the failed sources, in diagnostic order) come first and
    the secondary completeness targets (in ``investigations[]`` order) after
    them. The caller may shorten the queue; the order is the prioritization.
    Raises :class:`DiagnosticError` when the global status is inconclusive.
    """
    return select_targets(loaded) + select_secondary_targets(loaded)


def _read_payload(diagnostic_path: Path) -> dict:
    """Read the file and parse it as a JSON object, or raise ``DiagnosticError``."""
    try:
        text = diagnostic_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DiagnosticError(
            f"no se encontró el diagnóstico «{diagnostic_path}»: el fichero no "
            "existe; genéralo con «python -m verification.verify_run --offline»"
        ) from exc
    except OSError as exc:
        raise DiagnosticError(
            f"no se pudo leer el diagnóstico «{diagnostic_path}»: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise DiagnosticError(
            f"no se pudo leer el diagnóstico «{diagnostic_path}»: no es un "
            f"fichero UTF-8 válido ({exc})"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DiagnosticError(
            f"el diagnóstico «{diagnostic_path}» no contiene JSON válido: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise DiagnosticError(
            f"el diagnóstico «{diagnostic_path}» no es un objeto JSON"
        )
    return payload


def _validate_schema_version(payload: dict, diagnostic_path: Path) -> None:
    """Reject a diagnostic without the expected ``schema_version`` (plan §4)."""
    version = payload.get("schema_version", _MISSING)
    if version is _MISSING:
        raise DiagnosticError(
            f"el diagnóstico «{diagnostic_path}» no declara schema_version "
            f"(se esperaba {SCHEMA_VERSION})"
        )
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != SCHEMA_VERSION
    ):
        raise DiagnosticError(
            f"el diagnóstico «{diagnostic_path}» usa schema_version "
            f"{version!r} y se esperaba {SCHEMA_VERSION}"
        )


def _run_date(payload: dict) -> date | None:
    """Return the ``run.date`` of the diagnostic, or ``None`` when unusable.

    A missing or malformed date simply disables the freshness comparison: an
    unknown date is never reported as staleness.
    """
    run = payload.get("run")
    if not isinstance(run, dict):
        return None
    raw = run.get("date")
    if not isinstance(raw, str) or not _DATE_RE.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _latest_upload_log(scan_dir: Path) -> tuple[str, date] | None:
    """Return ``(name, date)`` of the newest ``upload-*.log``, or ``None``.

    Scan errors (a missing or unreadable directory) and names without a usable
    date are ignored: without upload logs there is nothing to compare against.
    """
    try:
        names = [entry.name for entry in scan_dir.iterdir()]
    except OSError:
        return None
    latest: tuple[str, date] | None = None
    for name in names:
        match = _UPLOAD_LOG_RE.fullmatch(name)
        if match is None:
            continue
        try:
            moment = date.fromisoformat(match.group("date"))
        except ValueError:
            continue
        if latest is None or moment > latest[1]:
            latest = (name, moment)
    return latest


def _freshness_warnings(payload: dict, scan_dir: Path) -> tuple[str, ...]:
    """Return the Spanish warning when the diagnostic predates the last upload."""
    run_date = _run_date(payload)
    if run_date is None:
        return ()
    latest = _latest_upload_log(scan_dir)
    if latest is None:
        return ()
    upload_name, upload_date = latest
    if run_date >= upload_date:
        return ()
    return (
        f"el diagnóstico es más antiguo (run {run_date.isoformat()}) que el "
        f"último registro de subida {upload_name} ({upload_date.isoformat()}); "
        "puede haber un run posterior sin diagnosticar: refresca con «python -m "
        "verification.verify_run --offline»",
    )


# --- Target selection helpers (T-02; RF-1) -----------------------------------


def _global_status(payload: dict) -> str | None:
    """Return ``global_status.status`` when it is a usable string."""
    global_status = payload.get("global_status")
    if not isinstance(global_status, dict):
        return None
    return _optional_str(global_status.get("status"))


def _global_reason(payload: dict) -> str | None:
    """Return ``global_status.reason`` when it is a usable string."""
    global_status = payload.get("global_status")
    if not isinstance(global_status, dict):
        return None
    return _optional_str(global_status.get("reason"))


def _inconclusive_message(loaded: LoadedDiagnostic) -> str:
    """Build the Spanish rejection message for an inconclusive diagnostic."""
    message = (
        f"el diagnóstico «{loaded.path}» tiene estado global inconcluso "
        f"({GLOBAL_INCONCLUSIVE}): no se inicia ninguna reparación"
    )
    reason = _global_reason(loaded.payload)
    if reason is not None:
        message += f"; motivo: {reason}"
    return message


def _source_entries(payload: dict) -> tuple[dict, ...]:
    """Return the usable source mappings of the diagnostic, in order.

    A missing or malformed ``sources`` value yields no entries and non-mapping
    items are ignored: extraction never crashes on a partial payload.
    """
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        return ()
    return tuple(entry for entry in raw_sources if isinstance(entry, dict))


def _target_from_source(
    entry: dict,
    loaded: LoadedDiagnostic,
    run_date: date | None,
) -> RepairTarget | None:
    """Build the target of a failed source, or ``None`` when it is not one."""
    if entry.get("status") != SOURCE_FAILED:
        return None
    source_id = _optional_str(entry.get("id"))
    if source_id is None:
        return None
    return RepairTarget(
        source=source_id,
        kind=_optional_str(entry.get("kind")),
        status=SOURCE_FAILED,
        outcome=_optional_str(entry.get("outcome")),
        offers_current_run=_optional_int(entry.get("offers_current_run")),
        offers_snapshot=_optional_int(entry.get("offers_snapshot")),
        failures=_string_tuple(entry.get("failures")),
        evidence=_string_tuple(entry.get("evidence")),
        evidence_paths=_evidence_paths(source_id, loaded),
        run_date=run_date,
        quality=_quality_profile(entry, source_id, focus_field=None),
    )


def _investigation_entries(payload: dict) -> tuple[dict, ...]:
    """Return the usable investigation mappings, in diagnostic order.

    A missing or malformed ``investigations`` value yields no entries and
    non-mapping items are ignored, mirroring :func:`_source_entries`.
    """
    investigations = payload.get("investigations")
    if not isinstance(investigations, list):
        return ()
    return tuple(
        entry for entry in investigations if isinstance(entry, dict)
    )


def _source_by_id(payload: dict, source_id: str) -> dict | None:
    """Return the ``sources[]`` entry of ``source_id``, or ``None``."""
    for entry in _source_entries(payload):
        if entry.get("id") == source_id:
            return entry
    return None


def _secondary_target_from_investigation(
    entry: dict,
    loaded: LoadedDiagnostic,
    run_date: date | None,
) -> RepairTarget | None:
    """Build the secondary target of a completeness investigation, or ``None``.

    ``source_failed`` investigations and entries without a usable source or
    field are ignored. The source context is taken from the matching
    ``sources[]`` entry when it exists; an investigation whose source is
    absent keeps the investigation data and reports ``SOURCE_UNKNOWN``.
    """
    trigger = _optional_str(entry.get("trigger"))
    if trigger not in SECONDARY_TRIGGERS:
        return None
    source_id = _optional_str(entry.get("source"))
    field = _optional_str(entry.get("field"))
    if source_id is None or field is None:
        return None
    source_entry = _source_by_id(loaded.payload, source_id) or {}
    return RepairTarget(
        source=source_id,
        kind=_optional_str(source_entry.get("kind")),
        status=_optional_str(source_entry.get("status")) or SOURCE_UNKNOWN,
        outcome=_optional_str(source_entry.get("outcome")),
        offers_current_run=_optional_int(source_entry.get("offers_current_run")),
        offers_snapshot=_optional_int(source_entry.get("offers_snapshot")),
        failures=_string_tuple(source_entry.get("failures")),
        evidence=_string_tuple(source_entry.get("evidence")),
        evidence_paths=_evidence_paths(source_id, loaded),
        run_date=run_date,
        field=field,
        trigger=trigger,
        current_pct=_current_pct(source_entry, field),
        role=ROLE_SECONDARY,
        quality=_quality_profile(source_entry, source_id, focus_field=field),
    )


def _quality_profile(
    source_entry: dict,
    source_id: str,
    focus_field: str | None,
) -> tuple[QualityProfile, ...]:
    """Build the per-field quality profile of one target (T-04; RF-1, RF-7).

    The profile follows the canonical ``field_contract.MEASURED_FIELDS`` order
    and is built only from the contract and the diagnostic: ``current_pct`` is
    the source's own ``completeness[].pct`` (``None`` when not measured, never
    invented) and ``target_pct`` is 100.0 for a required field and for an
    optional field the source publishes (``sources.field_aliases`` non-empty;
    plan §2.5, §9). An optional field the portal does not publish gets no
    target, so its absence is not a failure (RF-16). An unknown source has no
    aliases in the catalog, so only its required fields get a target.
    ``focus_field`` marks the secondary investigation field.
    """
    current = _completeness_by_field(source_entry)
    profile: list[QualityProfile] = []
    for field in field_contract.MEASURED_FIELDS:
        required = field_contract.is_required(field)
        published = bool(_field_aliases(source_id, field))
        profile.append(
            QualityProfile(
                field=field,
                required=required,
                current_pct=current.get(field),
                target_pct=100.0 if required or published else None,
                is_focus=field == focus_field,
            )
        )
    return tuple(profile)


def _field_aliases(source_id: str, field: str) -> tuple[str, ...]:
    """Return the source's origin aliases for a field, or ``()``.

    An unknown source or field yields no aliases: the catalog does not claim
    the portal publishes it, so no target is invented (plan §6.7).
    """
    try:
        return tuple(sources.field_aliases(source_id, field))
    except KeyError:
        return ()


def _completeness_by_field(source_entry: dict) -> dict[str, float | None]:
    """Return the source's measured field -> pct map, without inventing data.

    A missing or malformed ``completeness`` yields an empty map; an entry with
    a non-numeric ``pct`` is kept with ``None`` (the field was measured but
    the diagnostic carries no usable percentage). The first entry of a field
    wins, so a duplicated field never changes the reported value.
    """
    completeness = source_entry.get("completeness")
    if not isinstance(completeness, list):
        return {}
    measured: dict[str, float | None] = {}
    for item in completeness:
        if not isinstance(item, dict):
            continue
        field = _optional_str(item.get("field"))
        if field is not None and field not in measured:
            measured[field] = _optional_pct(item.get("pct"))
    return measured


def _current_pct(source_entry: dict, field: str) -> float | None:
    """Return the ``completeness[].pct`` of ``field``, or ``None``.

    The percentage is read from the source's own measurement; a missing entry
    or a non-numeric value yields ``None`` instead of an invented number.
    """
    return _completeness_by_field(source_entry).get(field)


def _optional_pct(value: object) -> float | None:
    """Return the value as a float when it is a number (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _evidence_paths(source_id: str, loaded: LoadedDiagnostic) -> tuple[str, ...]:
    """Return the local evidence paths of a target, playbook first (plan §2.3).

    Known sources contribute their playbook paths, relative to the repository
    root; an unknown source contributes no invented paths. Every target also
    carries the loaded diagnostic and, when the diagnostic records it,
    ``run.log_path``. Duplicates are removed preserving the first occurrence.
    """
    paths = list(PLAYBOOK_EVIDENCE_PATHS.get(source_id, ()))
    paths.append(str(loaded.path))
    run_log_path = _run_log_path(loaded.payload)
    if run_log_path is not None:
        paths.append(run_log_path)
    return _dedupe(paths)


def _run_log_path(payload: dict) -> str | None:
    """Return ``run.log_path`` when the diagnostic records a usable one."""
    run = payload.get("run")
    if not isinstance(run, dict):
        return None
    return _optional_str(run.get("log_path"))


def _string_tuple(value: object) -> tuple[str, ...]:
    """Coerce a diagnostic list of strings into a tuple, dropping noise."""
    if not isinstance(value, list):
        return ()
    return tuple(
        item for item in value if isinstance(item, str) and item.strip()
    )


def _optional_str(value: object) -> str | None:
    """Return the value when it is a non-empty string, else ``None``."""
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    """Return the value when it is an integer (never a bool), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _dedupe(items: list[str]) -> tuple[str, ...]:
    """Deduplicate strings preserving their first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)
