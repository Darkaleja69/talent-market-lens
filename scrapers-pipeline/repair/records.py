"""Open the record of one repair from its brief (T-13; RF-2, RF-11).

A repair starts by opening its record: the branch ``repair/<source>-<date>``
and the folder ``repairs/<YYYYMMDD>-<source>/`` (plan section 3.4). This
module instantiates ``repair/record_template.md`` with the English JSON brief
of the target (T-05), so the plan begins in state ``planificado`` with the
source and run, the observed failure, the diagnostic evidence, the
investigation playbook and the before/after quality profile already written.
The folder is created only when it does not exist: a record is never
overwritten (RF-2).

Files of a freshly created record (plan section 3.4)::

    repairs/<YYYYMMDD>-<source>/
      plan.md              # instantiated template, state planificado
      context.json         # the brief verbatim (T-05)
      quality_before.json  # per-field quality profile of the diagnostic
      evidence/            # empty; the web inspector fills it (T-19)

Design rules:

- The record date comes from the brief's ISO ``run_date`` (``YYYY-MM-DD`` to
  ``YYYYMMDD``). The optional ``on_date`` is the explicit opening date of the
  record (the "Registro creado" line, handy for tests) and the fallback date
  when the brief carries no usable run date. Without either date the creation
  fails with a clear Spanish error: the current date is never invented
  silently.
- The template placeholders are replaced in the whole file, including the
  title outside the section markers. The section markers of the template are
  preserved: T-14 replaces the content between them without losing anything.
- Creation is as atomic as the filesystem allows: the record directory is
  claimed with ``mkdir(exist_ok=False)`` before writing anything; if a later
  write fails, the directory created here is removed, so no half-written
  record is left behind. The parent ``repairs/`` directory is kept.
- ``quality_before.json`` is English and has the stable shape of
  ``repairs/example/quality_before.json``: identity plus ``fields``, sorted
  keys.
- The brief passes the T-05 safety gate before anything is written: a record
  never stores credentials or browser-profile paths (RF-11).

:func:`complete_record` closes a record (T-14, RF-11): it replaces the pending
sections of ``plan.md`` with the result, the changes, the tests and the live
test, stores the live measurement as ``quality_after.json`` and updates one row
of ``repairs/README.md`` and the per-source entry of ``repairs/history.json``.
Only a ``probado`` repair raises the verified count of the source (RF-10); a
``descartado`` or ``escalado`` repair records its outcome without lowering or
raising the bar. All new content is rendered in memory before anything is
written, and a failed write restores the previous files.

:func:`audit_records` (T-15, RF-11) is the hygiene safety net: it scans the
text files of the records tree (``plan.md``, the JSON files, ``evidence/**``,
``README.md``, ``history.json``) and reports credentials, tokens or
browser-profile paths as findings instead of raising. Binary evidence is
skipped; the free texts of T-13/T-14 are therefore covered end to end.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from repair import brief as brief_module
from repair import quality as quality_module
from repair import threshold

# repair/records.py -> parents[2] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPAIRS_DIR = REPO_ROOT / "repairs"
DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "record_template.md"
# repairs/README.md holds the repair index between the index markers (T-12).
DEFAULT_INDEX_PATH = DEFAULT_REPAIRS_DIR / "README.md"

# State of a freshly opened record (RF-2); T-14 moves it to probado,
# descartado or escalado.
STATUS_PLANNED = "planificado"

# Final states of a completed record (RF-11); only ``probado`` raises the
# verified count of the source (RF-10).
STATUS_TESTED = "probado"
STATUS_DISCARDED = "descartado"
STATUS_ESCALATED = "escalado"
FINAL_STATUSES = (STATUS_TESTED, STATUS_DISCARDED, STATUS_ESCALATED)

# quality_before.json contract (English, same shape as the sanitized example).
QUALITY_BEFORE_SCHEMA_VERSION = 1

# The record date is a plain ISO date; the folder uses the compact form.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Source ids name folders: letters, digits and underscore only, so an id can
# never escape the repairs directory (``../x``, ``a/b``).
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"\{\{[a-z_]+\}\}")
_EVIDENCE_DIR_NAME = "evidence"
_QUALITY_AFTER_NAME = "quality_after.json"

# A record directory opened by T-13: <YYYYMMDD>-<source>.
_RECORD_DIR_RE = re.compile(r"^(?P<date>\d{8})-(?P<source>[A-Za-z0-9_]+)$")
# The index table lives between these stable markers (T-12).
_INDEX_RE = re.compile(
    r"(?P<open><!--\s*repair-index:start\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<close><!--\s*repair-index:end\s*-->)",
    re.DOTALL,
)
_INDEX_HEADER = "| fecha | fuente | estado | rama | resultado | calidad |"
_INDEX_SEPARATOR = "|---|---|---|---|---|---|"

# Spanish labels of the quality row statuses; the codes stay English.
_QUALITY_STATUS_ES = {
    quality_module.STATUS_PASS: "cumple",
    quality_module.STATUS_FAIL: "no cumple",
    quality_module.STATUS_NOT_APPLICABLE: "no aplica",
}
_QUALITY_TABLE_HEADER = (
    "| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |"
)
_QUALITY_TABLE_SEPARATOR = "|---|---|---|---|---|---|---|"


class RecordError(RuntimeError):
    """A record could not be opened: existing record, missing date or I/O."""


@dataclass(frozen=True)
class RepairRecord:
    """Paths and identity of a freshly opened repair record (T-13; RF-2).

    ``directory`` is the record folder ``repairs/<YYYYMMDD>-<source>/``,
    ``plan_path``/``context_path``/``quality_before_path`` its files and
    ``evidence_dir`` the (empty) evidence directory. ``source`` is the id of
    the repaired source, ``date`` the record date (the brief's run date, or
    the explicit ``on_date`` when the brief has no usable one) and ``branch``
    the fix branch ``repair/<source>-<YYYYMMDD>`` of the record.
    """

    directory: Path
    plan_path: Path
    context_path: Path
    quality_before_path: Path
    evidence_dir: Path
    source: str
    date: date
    branch: str

    @property
    def quality_after_path(self) -> Path:
        """Path of ``quality_after.json`` (T-14 stores the live measurement)."""
        return self.directory / _QUALITY_AFTER_NAME


def create_record(
    brief: dict[str, object],
    *,
    repairs_dir: str | Path | None = None,
    template_path: str | Path | None = None,
    on_date: date | datetime | str | None = None,
) -> RepairRecord:
    """Open ``repairs/<YYYYMMDD>-<source>/`` from a brief and return its paths.

    The folder name is built from the brief's ``run_date`` (RF-2, plan
    section 3.4); ``on_date`` is the explicit opening date written as
    "Registro creado" and the fallback date when the brief has no usable run
    date (tests pin it so the content does not depend on the wall clock).
    ``repairs_dir`` and ``template_path`` default to the repository-anchored
    ``repairs/`` and the packaged ``record_template.md``.

    The brief is validated with the T-05 safety gate and the template is
    rendered in memory before touching the filesystem. An existing record
    directory is never overwritten: a second creation raises
    :class:`RecordError` and leaves the existing content untouched. If a
    write fails after the directory is claimed, the directory created here is
    removed before re-raising.
    """
    if not isinstance(brief, dict):
        raise RecordError("el brief debe ser un objeto JSON para abrir el registro")
    try:
        brief_module.validate_brief(brief)
    except brief_module.BriefError as exc:
        # The T-05 safety gate is a record error here: the brief cannot be
        # stored. The Spanish message (credentials/profile paths) is kept.
        raise RecordError(str(exc)) from exc
    source = _source_id(brief)
    run_date = _parse_date(brief.get("run_date"), "run_date del brief")
    opening_date = _parse_date(on_date, "on_date")
    record_date = run_date if run_date is not None else opening_date
    if record_date is None:
        raise RecordError(
            "el brief no trae un run_date ISO utilizable (YYYY-MM-DD) y no se "
            "indicó on_date; no se inventa la fecha del registro"
        )
    if opening_date is None:
        opening_date = date.today()

    base_dir = (
        Path(repairs_dir) if repairs_dir is not None else DEFAULT_REPAIRS_DIR
    )
    template = (
        Path(template_path)
        if template_path is not None
        else DEFAULT_TEMPLATE_PATH
    )
    directory = base_dir / f"{record_date:%Y%m%d}-{source}"
    plan_path = directory / "plan.md"
    context_path = directory / "context.json"
    quality_before_path = directory / "quality_before.json"
    evidence_dir = directory / _EVIDENCE_DIR_NAME
    branch = f"repair/{source}-{record_date:%Y%m%d}"

    if directory.exists():
        raise RecordError(
            f"ya existe un registro en «{directory}»: no se sobrescribe"
        )
    # Render everything (and validate the quality profile) before creating the
    # directory: a malformed brief or a missing template leaves no leftovers.
    plan_text = _render_plan(
        _read_template(template),
        brief,
        source=source,
        run_date=(run_date if run_date is not None else record_date).isoformat(),
        branch=branch,
        opening_date=opening_date,
    )
    quality_before = _quality_before(brief, source=source)

    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RecordError(
            f"ya existe un registro en «{directory}»: no se sobrescribe"
        ) from exc
    except OSError as exc:
        raise RecordError(
            f"no se pudo crear el directorio del registro «{directory}»: {exc}"
        ) from exc

    try:
        _write_text(plan_path, plan_text)
        _write_text(
            context_path, brief_module.render_brief(brief) + "\n"
        )
        _write_text(quality_before_path, _render_json(quality_before))
        evidence_dir.mkdir()
    except Exception as exc:
        # No half-written record: remove only the directory claimed above.
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, RecordError):
            raise
        raise RecordError(
            f"no se pudo crear el registro «{directory}»: {exc}"
        ) from exc

    return RepairRecord(
        directory=directory,
        plan_path=plan_path,
        context_path=context_path,
        quality_before_path=quality_before_path,
        evidence_dir=evidence_dir,
        source=source,
        date=record_date,
        branch=branch,
    )


def complete_record(
    record: RepairRecord | str | Path,
    *,
    status: str,
    result: str,
    changes: str | None = None,
    tests: str | None = None,
    live_test: str | None = None,
    verified_offers: int | None = None,
    quality_after: quality_module.QualityVerdict | dict | None = None,
    index_path: str | Path | None = None,
    history_path: str | Path | None = None,
) -> RepairRecord:
    """Close a repair record and update the index and the history (T-14; RF-11).

    ``record`` is the :class:`RepairRecord` opened by :func:`create_record` or
    its directory (``repairs/<YYYYMMDD>-<source>/``). ``status`` is the final
    state (``probado``, ``descartado`` or ``escalado``) and ``result`` the
    short Spanish outcome for the person. ``changes``, ``tests`` and
    ``live_test`` are the Spanish markdown bodies of their plan sections
    (``live_test`` documents the scope and the count); a missing body gets an
    explicit "nothing recorded" note instead of an invented one.

    ``verified_offers`` is the offer count the live test obtained: required
    (integer ≥ 1) for a ``probado`` repair and optional otherwise. ``quality_after``
    is a :class:`quality.QualityVerdict` or the dict of
    ``quality.verdict_to_dict``; it is validated against the record source and
    stored as ``quality_after.json`` with the stable English format. When it is
    omitted, an existing ``quality_after.json`` of the record is reused, so a
    second call does not drop the measurement.

    ``index_path`` and ``history_path`` default to ``repairs/README.md`` and
    ``repairs/history.json`` anchored to the repository root. The index gets
    exactly one row per ``(fecha, fuente)``, sorted by date and source; a
    missing index or one without its stable markers is a Spanish
    :class:`RecordError` raised before anything is written. The history entry
    of the source is enriched with ``last_result``, ``last_run_date``,
    ``quality_ok`` and ``quality_delta_pp`` (the worst known field delta);
    only a ``probado`` repair raises ``best_verified_offers`` (RF-10), and the
    unknown keys already stored are kept.

    All the new content is rendered (and every input validated) before the
    filesystem is touched; if a write fails, the previous content of the
    record, the index and the history is restored.
    """
    opened = _as_record(record)
    final_status = _final_status(status)
    result_text = _section_text(result, "result")
    changes_text = _section_text(changes, "changes", optional=True)
    tests_text = _section_text(tests, "tests", optional=True)
    live_test_text = _section_text(live_test, "live_test", optional=True)
    offers = _verified_count(verified_offers, final_status)

    plan_text = _read_text(opened.plan_path, "el plan del registro")
    before_entries = _load_quality_before(opened.quality_before_path)
    quality_payload = _resolve_quality_after(quality_after, opened)

    index_file = (
        Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    )
    history_file = (
        Path(history_path)
        if history_path is not None
        else threshold.DEFAULT_HISTORY_PATH
    )
    index_text = _read_text(index_file, "el índice de reparaciones")
    new_index_text = _updated_index(
        index_text,
        index_file,
        opened,
        final_status,
        result_text,
        quality_payload,
    )

    loaded_history = threshold.load_history(history_file)
    new_history = _updated_history(
        loaded_history.history, opened, final_status, offers, quality_payload
    )

    new_plan_text = _updated_plan(
        plan_text,
        changes_text=changes_text,
        tests_text=tests_text,
        live_test_text=live_test_text,
        quality_payload=quality_payload,
        before_entries=before_entries,
        result_text=result_text,
        status=final_status,
        offers=offers,
    )

    documents: list[tuple[Path, str]] = []
    if quality_payload is not None and quality_after is not None:
        documents.append(
            (opened.quality_after_path, _render_json(quality_payload))
        )
    documents.append((opened.plan_path, new_plan_text))
    documents.append((index_file, new_index_text))

    originals = {
        path: (path.read_bytes() if path.exists() else None)
        for path, _ in documents
    }
    history_original = (
        history_file.read_bytes() if history_file.exists() else None
    )
    try:
        for path, text in documents:
            _write_text(path, text)
        threshold.save_history(new_history, history_file)
    except Exception as exc:
        _restore(documents, originals, history_file, history_original)
        if isinstance(exc, RecordError):
            raise
        raise RecordError(
            f"no se pudo completar el registro «{opened.directory}»: {exc}"
        ) from exc
    return opened


# --- Hygiene audit (T-15; RF-11) ----------------------------------------------


@dataclass(frozen=True)
class HygieneFinding:
    """One hygiene finding of the records tree (T-15; RF-11).

    ``path`` is the file where the forbidden content lives and ``reason`` the
    Spanish motive. Findings are informative data: :func:`audit_records` never
    raises because of them and never echoes a secret value.
    """

    path: Path
    reason: str


# Binary evidence (screenshots, PDFs, archives) is never decoded as text: a
# known binary suffix or a NUL byte marks the file and it is skipped.
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".tif",
        ".tiff",
        ".pdf",
        ".zip",
        ".gz",
        ".7z",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".mp3",
        ".mp4",
        ".webm",
    }
)


def audit_records(
    repairs_dir: str | Path | None = None,
) -> tuple[HygieneFinding, ...]:
    """Audit the records tree for credentials and browser profiles (T-15).

    Walks every file under ``repairs_dir`` (by default the repository-anchored
    ``repairs/``): the text files of each record (``plan.md``,
    ``context.json``, ``quality_before.json``, ``quality_after.json``), its
    ``evidence/**`` files and the root ``README.md`` and ``history.json``.
    JSON files are additionally checked with the T-05 structural gate
    (:func:`repair.brief.find_forbidden`) and every text file with
    :func:`repair.brief.find_forbidden_text`, which flags assigned secret
    values without rejecting technical prose.

    Binary files (a known binary suffix or a NUL byte) are skipped; an
    unreadable, undecodable or malformed file is reported as an informative
    finding, never as an exception. The result is deterministic: findings in
    walk order, empty when the tree is clean (RF-11).
    """
    base = (
        Path(repairs_dir) if repairs_dir is not None else DEFAULT_REPAIRS_DIR
    )
    if not base.is_dir():
        return (
            HygieneFinding(
                base, f"no existe el directorio de registros «{base}»"
            ),
        )
    findings: list[HygieneFinding] = []
    for path in sorted(base.rglob("*")):
        if path.is_file():
            findings.extend(_audit_file(path))
    return tuple(findings)


def _audit_file(path: Path) -> list[HygieneFinding]:
    """Return the hygiene findings of one record file (T-15)."""
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [HygieneFinding(path, f"no se pudo leer el fichero: {exc}")]
    if b"\x00" in raw:
        # Binary content (for example a screenshot): never read as text.
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [
            HygieneFinding(
                path, f"el fichero no es texto UTF-8 legible: {exc}"
            )
        ]
    reasons = list(brief_module.find_forbidden_text(text))
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            reasons.append(f"el fichero JSON no es válido: {exc}")
        else:
            reasons = list(brief_module.find_forbidden(payload)) + reasons
    return [HygieneFinding(path, reason) for reason in dict.fromkeys(reasons)]


def _source_id(brief: dict[str, object]) -> str:
    """Return the validated source id, or raise a clear Spanish error."""
    source = brief.get("source")
    if not isinstance(source, str) or not source:
        raise RecordError(
            "el brief no trae un id de fuente («source») de texto no vacío"
        )
    if _SOURCE_RE.fullmatch(source) is None:
        raise RecordError(
            f"el id de fuente «{source}» no es válido para un registro: usa "
            "letras, dígitos o guion bajo"
        )
    return source


def _parse_date(value: object, field_name: str) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date (or a ``date``) into a date, or None.

    A date-like object is accepted; a string must be exactly ``YYYY-MM-DD``
    and a real calendar date; any other value is a :class:`RecordError` with a
    Spanish message.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if _DATE_RE.fullmatch(value) is None:
            raise RecordError(
                f"«{field_name}» no es una fecha ISO válida (YYYY-MM-DD): "
                f"«{value}»"
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RecordError(
                f"«{field_name}» no es una fecha real del calendario: "
                f"«{value}»"
            ) from exc
    raise RecordError(
        f"«{field_name}» debe ser una fecha ISO YYYY-MM-DD, no "
        f"{type(value).__name__}"
    )


def _read_template(template: Path) -> str:
    """Read the record template as UTF-8 text, or raise a Spanish error."""
    try:
        return template.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(
            f"no se pudo leer la plantilla del registro «{template}»: {exc}"
        ) from exc


def _render_plan(
    template_text: str,
    brief: dict[str, object],
    *,
    source: str,
    run_date: str,
    branch: str,
    opening_date: date,
) -> str:
    """Instantiate the template with the brief content (T-13).

    The four placeholders are replaced in the whole file (the title lives
    outside the section markers) and the failure, evidence, investigation and
    quality sections are filled from the brief. Changes, tests, live test and
    result keep their pending notes: T-14 completes them. A leftover
    placeholder is an error, never written.
    """
    text = template_text
    for placeholder, value in (
        ("{{source}}", source),
        ("{{run_date}}", run_date),
        ("{{branch}}", branch),
        ("{{status}}", STATUS_PLANNED),
    ):
        text = text.replace(placeholder, value)
    text = text.replace("(fecha de apertura)", opening_date.isoformat())
    text = _fill_section(text, "failure", _failure_body(brief))
    text = _fill_section(text, "evidence", _evidence_body(brief))
    text = _fill_section(text, "investigation", _investigation_body(brief))
    text = _fill_section(text, "quality", _quality_body(brief))
    remaining = _PLACEHOLDER_RE.search(text)
    if remaining is not None:
        raise RecordError(
            "quedó un marcador sin sustituir en el plan del registro: "
            f"«{remaining.group(0)}»"
        )
    if not text.endswith("\n"):
        text += "\n"
    return text


def _fill_section(text: str, section_id: str, body: str) -> str:
    """Replace the content between a section's markers, keeping the markers.

    T-14 uses the same stable markers to update a section without losing the
    rest of the file; the template must contain each section exactly once.
    """
    pattern = re.compile(
        rf"(?P<open><!--\s*record:section:{re.escape(section_id)}\s*-->)"
        r".*?"
        rf"(?P<close><!--\s*/record:section:{re.escape(section_id)}\s*-->)",
        re.DOTALL,
    )
    filled, count = pattern.subn(
        lambda match: (
            f"{match.group('open')}\n{body}\n{match.group('close')}"
        ),
        text,
        count=1,
    )
    if count != 1:
        raise RecordError(
            f"la plantilla del registro no tiene la sección «{section_id}»: "
            f"{count} coincidencias"
        )
    return filled


def _failure_body(brief: dict[str, object]) -> str:
    """Render the observed failure from the brief reason (Spanish)."""
    lines = ["## Fallo observado", ""]
    role = brief.get("role")
    role_text = {
        "primary": "primary (fuente fallida)",
        "secondary": "secondary (investigación de completitud)",
    }.get(role if isinstance(role, str) else "", str(role))
    kind = brief.get("kind")
    if isinstance(kind, str) and kind:
        role_text += f" — tipo: {kind}"
    lines.append(f"- **Rol:** {role_text}")
    reason = brief.get("reason")
    if isinstance(reason, dict) and "outcome" in reason:
        lines.append(
            f"- **Resultado (outcome):** {_string_or(reason.get('outcome'))}"
        )
        lines.append(
            f"- **Resumen:** {_string_or(reason.get('summary'))}"
        )
        failures = reason.get("failures")
        if isinstance(failures, list) and failures:
            lines.append(
                "- **Motivos del diagnóstico:** "
                + "; ".join(f"«{_string_or(item)}»" for item in failures)
            )
        else:
            lines.append("- **Motivos del diagnóstico:** sin motivos registrados")
    elif isinstance(reason, dict):
        lines.append(
            f"- **Trigger:** {_string_or(reason.get('trigger'))}"
        )
        lines.append(
            f"- **Campo:** `{_string_or(reason.get('field'))}` "
            f"({_pct_text(reason.get('current_pct'))})"
        )
        lines.append(
            f"- **Resumen:** {_string_or(reason.get('summary'))}"
        )
    else:
        lines.append("- **Motivo:** el brief no trae un motivo estructurado")
    lines.append(
        f"- **Ofertas del run:** {_count_text(brief.get('offers_current_run'))}"
    )
    snapshot = brief.get("offers_snapshot")
    if snapshot is None:
        lines.append("- **Ofertas del snapshot:** no aplica")
    else:
        scope = brief.get("completeness_scope")
        lines.append(
            f"- **Ofertas del snapshot:** {_count_text(snapshot)} "
            f"(agregado; alcance: {_string_or(scope)})"
        )
    return "\n".join(lines)


def _evidence_body(brief: dict[str, object]) -> str:
    """Render the diagnostic evidence and the local evidence paths."""
    lines = ["## Evidencia", "", "- **Evidencia del diagnóstico:**"]
    evidence = brief.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.extend(f"  - «{_string_or(item)}»" for item in evidence)
    else:
        lines.append("  - sin evidencia registrada")
    header = "- **Rutas locales de referencia**"
    if brief.get("evidence_paths_base") == "repository_root":
        header += " (relativas a la raíz del repositorio)"
    lines.append(f"{header}:")
    paths = brief.get("evidence_paths")
    if isinstance(paths, list) and paths:
        lines.extend(f"  - `{_string_or(item)}`" for item in paths)
    else:
        lines.append("  - sin rutas registradas")
    return "\n".join(lines)


def _investigation_body(brief: dict[str, object]) -> str:
    """Render the applicable playbook and the proposed test scope."""
    lines = ["## Plan de investigación", ""]
    playbook = brief.get("playbook")
    if isinstance(playbook, dict):
        section = _string_or(playbook.get("section"))
        title = _string_or(playbook.get("title"))
        lines.append(f"- **Playbook:** §{section} — {title}")
        lines.append(f"- **Receta:** {_string_or(playbook.get('recipe'))}")
    else:
        lines.append("- **Playbook:** el brief no trae playbook")
    scope = brief.get("test_scope")
    if isinstance(scope, dict):
        lines.append(
            "- **Alcance de prueba propuesto:** "
            f"{_string_or(scope.get('description'))}"
        )
        constraints = scope.get("constraints")
        if isinstance(constraints, list) and constraints:
            lines.append(
                "- **Restricciones:** "
                + "; ".join(_string_or(item) for item in constraints)
            )
        command = scope.get("command")
        if isinstance(command, str) and command:
            lines.append(f"- **Comando propuesto:** `{command}`")
    lines.append(
        "- **Pendiente:** comprobar robots.txt y términos de uso, separar "
        "hechos de hipótesis y confirmar la causa probable (RF-3)."
    )
    return "\n".join(lines)


def _quality_body(brief: dict[str, object]) -> str:
    """Render the before/after quality table with the pending measurement."""
    lines = [
        "## Calidad",
        "",
        "Tabla antes/después por campo (`quality_before.json` listo; "
        "`quality_after.json` pendiente de la prueba en vivo):",
        "",
        "| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |",
        "|---|---|---|---|---|---|---|",
    ]
    entries = _quality_entries(brief)
    if entries:
        for entry in entries:
            lines.append(
                f"| {entry['field']} "
                f"| {'sí' if entry['required'] else 'no'} "
                f"| {_pct_text(entry['current_pct'])} "
                "| pendiente | pendiente "
                f"| {_target_text(entry['target_pct'])} "
                "| pendiente |"
            )
    else:
        lines.append("| _(pendiente)_ | | | | | | |")
    lines.extend(
        [
            "",
            "_(Pendiente: veredicto de `quality.py` y `quality_after.json`.)_",
        ]
    )
    return "\n".join(lines)


def _quality_entries(brief: dict[str, object]) -> list[dict[str, object]]:
    """Extract the brief quality profile into stable English field entries.

    A brief without ``quality`` yields an empty profile; a malformed profile
    (not a list, or an entry without a field name) is a :class:`RecordError`
    instead of a silent empty record.
    """
    quality = brief.get("quality")
    if quality is None:
        return []
    if not isinstance(quality, list):
        raise RecordError(
            "el perfil de calidad del brief («quality») no es una lista"
        )
    entries: list[dict[str, object]] = []
    for item in quality:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("field"), str)
            or not item["field"]
        ):
            raise RecordError(
                "hay una entrada del perfil de calidad del brief sin campo "
                "(«field»)"
            )
        entries.append(
            {
                "field": item["field"],
                "required": bool(item.get("required")),
                "current_pct": _optional_number(item.get("current_pct")),
                "target_pct": _optional_number(item.get("target_pct")),
                "is_focus": bool(item.get("is_focus")),
            }
        )
    return entries


def _quality_before(
    brief: dict[str, object], *, source: str
) -> dict[str, object]:
    """Build ``quality_before.json``: identity plus the per-field profile."""
    return {
        "schema_version": QUALITY_BEFORE_SCHEMA_VERSION,
        "source": source,
        "run_date": brief.get("run_date"),
        "offers_current_run": brief.get("offers_current_run"),
        "offers_snapshot": brief.get("offers_snapshot"),
        "completeness_scope": brief.get("completeness_scope"),
        "fields": _quality_entries(brief),
    }


def _render_json(payload: dict[str, object]) -> str:
    """Serialize a machine file with the stable English formatting."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 with Unix newlines, so Windows and CI agree byte by byte."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _optional_number(value: object) -> float | int | None:
    """Return a number as is (bool excluded), or ``None`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _pct_text(value: object) -> str:
    """Format a percentage with one decimal, or a Spanish missing note."""
    number = _optional_number(value)
    if number is None:
        return "sin dato"
    return f"{float(number):.1f} %"


def _target_text(value: object) -> str:
    """Format the quality target, or the explicit "sin meta" note."""
    number = _optional_number(value)
    if number is None:
        return "sin meta"
    return f"{float(number):.1f} %"


def _count_text(value: object) -> str:
    """Format an offer counter, or a Spanish missing note."""
    if isinstance(value, bool) or not isinstance(value, int):
        return "sin dato"
    return str(value)


def _string_or(value: object) -> str:
    """Return the value as text, with a Spanish fallback for ``None``."""
    return "sin dato" if value is None else str(value)


# --- Completing a record (T-14; RF-11) ---------------------------------------


def _as_record(record: RepairRecord | str | Path) -> RepairRecord:
    """Return the record, building its paths when a directory is given."""
    if isinstance(record, RepairRecord):
        return record
    directory = Path(record)
    if not directory.is_dir():
        raise RecordError(
            f"no existe el directorio del registro «{directory}»"
        )
    match = _RECORD_DIR_RE.fullmatch(directory.name)
    if match is None:
        raise RecordError(
            f"el directorio «{directory.name}» no sigue el patrón "
            "«<YYYYMMDD>-<fuente>» de un registro"
        )
    try:
        record_date = datetime.strptime(match.group("date"), "%Y%m%d").date()
    except ValueError as exc:
        raise RecordError(
            f"la fecha del registro «{directory.name}» no es una fecha real "
            "del calendario"
        ) from exc
    source = match.group("source")
    return RepairRecord(
        directory=directory,
        plan_path=directory / "plan.md",
        context_path=directory / "context.json",
        quality_before_path=directory / "quality_before.json",
        evidence_dir=directory / _EVIDENCE_DIR_NAME,
        source=source,
        date=record_date,
        branch=f"repair/{source}-{record_date:%Y%m%d}",
    )


def _final_status(value: object) -> str:
    """Return the final status, or a Spanish error for an unknown one."""
    if not isinstance(value, str) or value not in FINAL_STATUSES:
        raise RecordError(
            "el estado final del registro debe ser uno de: "
            + ", ".join(FINAL_STATUSES)
        )
    return value


def _section_text(
    value: object, section_id: str, *, optional: bool = False
) -> str | None:
    """Validate a Spanish section body; a missing optional one returns None."""
    if value is None:
        if optional:
            return None
        raise RecordError(
            f"falta el texto de la sección «{section_id}» del registro"
        )
    if not isinstance(value, str) or not value.strip():
        raise RecordError(
            f"el texto de la sección «{section_id}» debe ser texto no vacío"
        )
    if "record:section:" in value:
        raise RecordError(
            f"el texto de la sección «{section_id}» no puede contener los "
            "marcadores «record:section»"
        )
    return value.strip()


def _verified_count(value: object, status: str) -> int | None:
    """Validate the verified count: mandatory (≥ 1) only for ``probado``."""
    if value is None:
        if status == STATUS_TESTED:
            raise RecordError(
                "una reparación probada necesita su recuento verificado "
                "(verified_offers) como entero ≥ 1"
            )
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecordError(
            "el recuento verificado (verified_offers) debe ser un entero, no "
            f"{type(value).__name__}"
        )
    minimum = 1 if status == STATUS_TESTED else 0
    if value < minimum:
        raise RecordError(
            f"el recuento verificado de una reparación {status} debe ser un "
            f"entero ≥ {minimum}, no {value}"
        )
    return value


def _read_text(path: Path, label: str) -> str:
    """Read a UTF-8 text file, or raise a clear Spanish error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(f"no se pudo leer {label} «{path}»: {exc}") from exc


def _read_json(path: Path, label: str) -> object:
    """Read and parse a UTF-8 JSON file, or raise a Spanish error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(f"no se pudo leer {label} «{path}»: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecordError(
            f"{label} «{path}» no contiene JSON válido: {exc}"
        ) from exc


def _load_quality_before(path: Path) -> list[dict[str, object]]:
    """Return the field entries of the stored ``quality_before.json``."""
    payload = _read_json(path, "quality_before.json")
    if not isinstance(payload, dict):
        raise RecordError(f"«{path}» no contiene un objeto JSON")
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise RecordError(
            f"«{path}» no trae la lista de campos («fields»)"
        )
    entries: list[dict[str, object]] = []
    for entry in fields:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("field"), str)
            or not entry["field"]
        ):
            raise RecordError(
                f"«{path}» tiene una fila de calidad sin campo («field»)"
            )
        entries.append(entry)
    return entries


def _resolve_quality_after(
    value: quality_module.QualityVerdict | dict | None, record: RepairRecord
) -> dict | None:
    """Validate the live measurement, or reuse the stored one when omitted.

    A measurement of another source is rejected: ``quality_after.json`` of a
    record is never overwritten with the verdict of a different source.
    """
    path = record.quality_after_path
    if value is None:
        if not path.exists():
            return None
        payload = _read_json(path, "quality_after.json")
        if not isinstance(payload, dict):
            raise RecordError(f"«{path}» no contiene un objeto JSON")
        _validate_quality_payload(payload, record)
        return payload
    if isinstance(value, quality_module.QualityVerdict):
        payload = quality_module.verdict_to_dict(value)
    elif isinstance(value, dict):
        payload = value
    else:
        raise RecordError(
            "la medición de calidad debe ser un QualityVerdict o un dict de "
            "quality.verdict_to_dict"
        )
    _validate_quality_payload(payload, record)
    return payload


def _validate_quality_payload(
    payload: dict, record: RepairRecord
) -> None:
    """Check the essential shape of a ``quality_after.json`` payload."""
    if payload.get("source") != record.source:
        raise RecordError(
            f"la medición de calidad es de la fuente «{payload.get('source')}» "
            f"y el registro es de «{record.source}»: no se sobrescribe"
        )
    if not isinstance(payload.get("ok"), bool):
        raise RecordError(
            "la medición de calidad no trae el veredicto «ok» booleano"
        )
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise RecordError(
            "la medición de calidad no trae la lista de campos («fields»)"
        )
    for entry in fields:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("field"), str)
            or not entry["field"]
        ):
            raise RecordError(
                "hay una fila de la medición de calidad sin campo («field»)"
            )
    blockers = payload.get("blockers")
    if blockers is not None and not isinstance(blockers, list):
        raise RecordError(
            "la medición de calidad no trae «blockers» como lista"
        )


def _updated_plan(
    text: str,
    *,
    changes_text: str | None,
    tests_text: str | None,
    live_test_text: str | None,
    quality_payload: dict | None,
    before_entries: list[dict[str, object]],
    result_text: str,
    status: str,
    offers: int | None,
) -> str:
    """Replace only the content of the five sections T-14 completes."""
    updated = text
    updated = _fill_section(
        updated,
        "changes",
        _narrative_body(
            "Cambios realizados",
            changes_text,
            "_(Sin cambios registrados en esta reparación.)_",
        ),
    )
    updated = _fill_section(
        updated,
        "tests",
        _narrative_body(
            "Pruebas: tests",
            tests_text,
            "_(Sin pruebas registradas.)_",
        ),
    )
    updated = _fill_section(
        updated,
        "live_test",
        _narrative_body(
            "Pruebas: prueba en vivo",
            live_test_text,
            "_(Sin prueba en vivo registrada.)_",
        ),
    )
    updated = _fill_section(
        updated,
        "quality",
        _quality_section_body(quality_payload, before_entries),
    )
    updated = _fill_section(
        updated,
        "result",
        _result_body(result_text, status, offers),
    )
    if not updated.endswith("\n"):
        updated += "\n"
    return updated


def _narrative_body(title: str, text: str | None, fallback: str) -> str:
    """Render a section body with the given text or the explicit fallback."""
    body = text if text is not None else fallback
    return f"## {title}\n\n{body}"


def _quality_section_body(
    payload: dict | None, before_entries: list[dict[str, object]]
) -> str:
    """Render the before/after quality table and the verdict (T-14).

    The "después" values come from ``quality_after``; the "antes" value of
    each field comes from the stored ``quality_before.json`` (falling back to
    the row's own ``before_pct`` when the field is not in the profile).
    """
    lines = ["## Calidad", ""]
    if payload is None:
        lines.append(
            "Tabla antes/después por campo (`quality_before.json`; "
            "`quality_after.json` sin medición):"
        )
        lines.append("")
        lines.extend(_before_only_rows(before_entries))
        lines.extend(
            [
                "",
                "_(Sin medición de calidad: la prueba en vivo no se completó.)_",
            ]
        )
        return "\n".join(lines)
    lines.append(
        "Tabla antes/después por campo (`quality_before.json` → "
        "`quality_after.json`):"
    )
    lines.append("")
    lines.extend(_after_rows(payload, before_entries))
    lines.extend(["", _verdict_text(payload)])
    return "\n".join(lines)


def _before_only_rows(entries: list[dict[str, object]]) -> list[str]:
    """Render the before profile with the measurement still pending."""
    lines = [_QUALITY_TABLE_HEADER, _QUALITY_TABLE_SEPARATOR]
    if not entries:
        lines.append("| _(sin perfil de calidad)_ | | | | | | |")
        return lines
    for entry in entries:
        lines.append(
            f"| {entry['field']} "
            f"| {'sí' if entry.get('required') else 'no'} "
            f"| {_pct_text(entry.get('current_pct'))} "
            "| pendiente | pendiente "
            f"| {_target_text(entry.get('target_pct'))} "
            "| pendiente |"
        )
    return lines


def _after_rows(
    payload: dict, before_entries: list[dict[str, object]]
) -> list[str]:
    """Render one markdown row per measured field of the verdict."""
    before_by_field = {
        entry["field"]: entry.get("current_pct") for entry in before_entries
    }
    lines = [_QUALITY_TABLE_HEADER, _QUALITY_TABLE_SEPARATOR]
    fields = payload.get("fields") or []
    if not fields:
        lines.append("| _(sin filas medidas)_ | | | | | | |")
        return lines
    for row in fields:
        field = row["field"]
        if field in before_by_field:
            before = before_by_field[field]
        else:
            before = row.get("before_pct")
        status = row.get("status")
        lines.append(
            f"| {field} "
            f"| {'sí' if row.get('required') else 'no'} "
            f"| {_pct_text(before)} "
            f"| {_pct_text(row.get('after_pct'))} "
            f"| {_delta_text(row.get('delta_pp'))} "
            f"| {_target_text(row.get('target_pct'))} "
            f"| {_QUALITY_STATUS_ES.get(status, _string_or(status))} |"
        )
    return lines


def _verdict_text(payload: dict) -> str:
    """Render the Spanish verdict line with the English blocker codes."""
    verdict = "OK" if payload.get("ok") else "NO OK"
    blockers = [str(code) for code in (payload.get("blockers") or [])]
    if blockers:
        return (
            f"Veredicto de `quality.py`: **{verdict}** — bloqueos: "
            + ", ".join(f"`{code}`" for code in blockers)
            + "."
        )
    return f"Veredicto de `quality.py`: **{verdict}**."


def _result_body(
    result_text: str, status: str, offers: int | None
) -> str:
    """Render the result, the verified count and the final state (RF-11)."""
    lines = ["## Resultado y estado", "", f"- **Resultado:** {result_text}"]
    if offers is not None:
        lines.append(f"- **Ofertas verificadas:** {offers}")
    if status == STATUS_TESTED:
        lines.append(
            "- **Validación del push:** pendiente de la persona (RF-12)"
        )
    else:
        lines.append(
            f"- **Validación del push:** no aplica (reparación {status})"
        )
    lines.append(f"- **Estado:** {status}")
    return "\n".join(lines)


def _updated_index(
    text: str,
    index_file: Path,
    record: RepairRecord,
    status: str,
    result_text: str,
    payload: dict | None,
) -> str:
    """Insert or update the single index row of ``(fecha, fuente)`` (T-14)."""
    match = _INDEX_RE.search(text)
    if match is None:
        raise RecordError(
            f"el índice «{index_file}» no tiene los marcadores "
            "«repair-index:start» y «repair-index:end»: no se actualiza nada"
        )
    body_lines = match.group("body").strip().splitlines()
    if (
        len(body_lines) < 2
        or body_lines[0] != _INDEX_HEADER
        or body_lines[1] != _INDEX_SEPARATOR
    ):
        raise RecordError(
            f"el índice «{index_file}» no tiene la cabecera esperada "
            f"«{_INDEX_HEADER}»"
        )
    key = (record.date.isoformat(), record.source)
    rows = [
        line
        for line in body_lines[2:]
        if line.strip() and _index_row_key(line) != key
    ]
    rows.append(_index_row(record, status, result_text, payload))
    rows.sort(key=_index_sort_key)
    body = "\n" + "\n".join([_INDEX_HEADER, _INDEX_SEPARATOR, *rows]) + "\n"
    return text[: match.start("body")] + body + text[match.end("body") :]


def _index_row(
    record: RepairRecord, status: str, result_text: str, payload: dict | None
) -> str:
    """Render the markdown row of a completed repair (six safe columns)."""
    summary = _single_line(result_text)
    return (
        f"| {record.date.isoformat()} | {record.source} | {status} "
        f"| {record.branch} | {summary} | {_index_quality_text(payload)} |"
    )


def _index_row_key(line: str) -> tuple[str, str] | None:
    """Return ``(fecha, fuente)`` of an index row, or None if malformed."""
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 4 or parts[0] or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _index_sort_key(line: str) -> tuple[str, str]:
    """Sort rows by date and source; malformed rows go last, untouched."""
    key = _index_row_key(line)
    return key if key is not None else ("\uffff", "\uffff")


def _index_quality_text(payload: dict | None) -> str:
    """Summarize the verdict for the ``calidad`` column in Spanish."""
    if payload is None:
        return "sin medición"
    label = "OK" if payload.get("ok") else "NO OK"
    fields = payload.get("fields") or []
    failed = [
        row["field"]
        for row in fields
        if row.get("status") == quality_module.STATUS_FAIL
    ]
    if failed:
        return f"{label}: fallan {', '.join(failed)}"
    passed = [
        row["field"]
        for row in fields
        if row.get("status") == quality_module.STATUS_PASS
    ]
    if passed:
        return f"{label}: cumplen {', '.join(passed)}"
    blockers = [str(code) for code in (payload.get("blockers") or [])]
    if blockers:
        return f"{label}: {', '.join(blockers)}"
    return label


def _single_line(text: str) -> str:
    """Collapse a Spanish summary into one markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _updated_history(
    history: dict,
    record: RepairRecord,
    status: str,
    offers: int | None,
    payload: dict | None,
) -> dict:
    """Enrich the source entry; only ``probado`` raises the verified count.

    ``record_verified`` keeps the maximum, so the bar never decreases (RF-10).
    A non-verified source with no entry gets ``best_verified_offers: 0`` (the
    explicit "no verified offers yet" floor: the next threshold is still 1);
    an existing value is never touched by a discarding or escalating repair.
    Unknown keys already stored in the entry are kept.
    """
    if status == STATUS_TESTED:
        history = threshold.record_verified(
            history, record.source, offers
        )
    new_history = dict(history)
    sources = new_history.get("sources")
    sources = dict(sources) if isinstance(sources, dict) else {}
    entry = sources.get(record.source)
    entry = dict(entry) if isinstance(entry, dict) else {}
    if "best_verified_offers" not in entry:
        entry["best_verified_offers"] = 0
    entry.update(
        {
            "last_result": status,
            "last_run_date": record.date.isoformat(),
            "quality_ok": None if payload is None else bool(payload.get("ok")),
            "quality_delta_pp": _worst_delta(payload),
        }
    )
    sources[record.source] = entry
    new_history["sources"] = sources
    return new_history


def _worst_delta(payload: dict | None) -> float | None:
    """Return the worst known field delta in points, or ``None``.

    The single number stored in the history is the smallest field delta of the
    measurement (the one that matters for the no-regression rule of RF-16);
    when no field has a before value there is no delta and ``None`` is stored.
    """
    if payload is None:
        return None
    deltas = []
    for row in payload.get("fields") or []:
        if not isinstance(row, dict):
            continue
        number = _optional_number(row.get("delta_pp"))
        if number is not None:
            deltas.append(float(number))
    return min(deltas) if deltas else None


def _delta_text(value: object) -> str:
    """Format a percentage-point delta with its sign, or a missing note."""
    number = _optional_number(value)
    if number is None:
        return "sin dato"
    return f"{float(number):+.1f} pp"


def _restore(
    documents: list[tuple[Path, str]],
    originals: dict[Path, bytes | None],
    history_file: Path,
    history_original: bytes | None,
) -> None:
    """Best-effort rollback of a failed completion (record, index, history)."""
    for path, _ in documents:
        original = originals.get(path)
        try:
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)
        except OSError:
            continue
    try:
        if history_original is None:
            if history_file.exists():
                history_file.unlink()
        else:
            history_file.write_bytes(history_original)
    except OSError:
        pass
