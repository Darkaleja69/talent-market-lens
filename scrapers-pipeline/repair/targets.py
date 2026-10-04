"""Read, validate and freshness-check the daily diagnostic (T-01; RF-1, RF-13).

The diagnostic written by the 001 process is the input of every repair: this
module loads ``diagnostic_last.json``, validates its contract
(``schema_version: 1``) and warns, without failing, when it is older than the
last ``upload-*.log`` of the logs directory (a symptom of a newer run that has
not been diagnosed yet; plan section 2.1).

The loaded payload is kept available so the following tasks can extract the
failed sources and the secondary investigations from it (T-02, T-03).

Design rules:

- A missing, unreadable or invalid diagnostic is a domain error
  (:class:`DiagnosticError`) with a clear Spanish message, never a crash with a
  raw ``OSError``/``JSONDecodeError``.
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

# Only the schema this process knows how to interpret is accepted (plan §4).
SCHEMA_VERSION = 1

# scrapers-pipeline/repair/targets.py -> parents[1] is `scrapers-pipeline`.
DEFAULT_DIAGNOSTIC_PATH = (
    Path(__file__).resolve().parents[1] / "logs" / "diagnostic_last.json"
)

# A fresh run leaves an `upload-YYYY-MM-DD.log` or `upload-<source>-YYYY-MM-DD.log`
# in the logs directory; a name without a usable date is ignored.
_UPLOAD_LOG_RE = re.compile(r"^upload-(?:.+?-)?(?P<date>\d{4}-\d{2}-\d{2})\.log$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MISSING = object()


class DiagnosticError(RuntimeError):
    """The diagnostic could not be loaded or does not honour its contract."""


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
