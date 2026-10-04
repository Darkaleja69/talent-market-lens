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

Only the creation is implemented here; completing a record and updating the
index/history is T-14.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from repair import brief as brief_module

# repair/records.py -> parents[2] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPAIRS_DIR = REPO_ROOT / "repairs"
DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent / "record_template.md"

# State of a freshly opened record (RF-2); T-14 moves it to probado,
# descartado or escalado.
STATUS_PLANNED = "planificado"

# quality_before.json contract (English, same shape as the sanitized example).
QUALITY_BEFORE_SCHEMA_VERSION = 1

# The record date is a plain ISO date; the folder uses the compact form.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Source ids name folders: letters, digits and underscore only, so an id can
# never escape the repairs directory (``../x``, ``a/b``).
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"\{\{[a-z_]+\}\}")
_EVIDENCE_DIR_NAME = "evidence"


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
