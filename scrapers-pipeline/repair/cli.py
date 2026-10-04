"""Thin CLI of the scraper-repair process (T-16; RF-13).

Entry point of spec 004, meant to run from ``scrapers-pipeline/`` as
``python -m repair.cli targets|brief|threshold|quality|record``. The command
only parses the arguments, calls the deterministic core modules and prints
their output:

- ``targets``: loads the diagnostic (default
  ``scrapers-pipeline/logs/diagnostic_last.json``) and prints the prioritized
  Spanish repair queue of :mod:`repair.presentation`.
- ``brief``: builds the English JSON brief of one target with
  :mod:`repair.brief`; a source with several candidates (for example Indeed,
  which has a primary target and its field investigations) requires
  ``--field`` to pick one.
- ``threshold``: shows the incremental threshold per source from
  ``repairs/history.json`` with :mod:`repair.threshold`.
- ``quality``: measures a live-test Parquet against the
  ``quality_before.json`` profile of a record with the gate of
  :mod:`repair.quality`.
- ``record``: opens a record from a brief (state ``planificado``) or completes
  it with its final status through :mod:`repair.records`.

Design rules (constitution #3/#6):

- No business rule lives here: argument parsing, input deserialization,
  output printing and exit codes only. The domain errors of the core modules
  (``targets.DiagnosticError``, ``brief.BriefError``, ``records.RecordError``)
  and the CLI file-read errors become a Spanish ``Error: ...`` line on stderr
  plus exit code 1; argparse keeps its own usage errors (exit code 2).
- :func:`main` never calls ``sys.exit``: it returns the exit code and the
  module guard raises ``SystemExit`` with it. The quality subcommand returns 1
  when the gate does not pass, even though the table is still printed.
- stdout/stderr are reconfigured to UTF-8 when the stream supports it, because
  the Spanish output of :mod:`repair.presentation` contains ``→`` and the
  Windows console may be cp1252. The reconfiguration is defensive: a stream
  without ``reconfigure`` or a failed call is left untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repair import brief, presentation, quality, records, targets, threshold

# Help texts shared by several subcommands; the defaults themselves live in
# the core modules, not here.
_DIAGNOSTIC_HELP = (
    "ruta del diagnóstico (por defecto, "
    "scrapers-pipeline/logs/diagnostic_last.json)"
)
_HISTORY_HELP = "ruta del historial (por defecto, repairs/history.json)"
_OUTPUT_HELP = "ruta del fichero JSON de salida"


class CliInputError(RuntimeError):
    """A CLI input file is missing, unreadable or breaks its expected shape."""


def main(argv: list[str] | None = None) -> int:
    """Run one repair CLI command and return its exit code (T-16; RF-13).

    ``argv`` defaults to ``sys.argv[1:]`` through argparse. Exit codes: 0 on
    success and on a quality verdict that passes the gate; 1 on a domain error
    or a failed quality gate; 2 on an argparse usage error. The function never
    calls ``sys.exit``.
    """
    _force_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "record":
        if args.brief is None and args.record is None:
            parser.error(
                "el modo del registro es obligatorio: indica «--brief» "
                "FICHERO o «--record» DIRECTORIO"
            )
        if args.record is not None:
            missing = [
                flag
                for flag, value in (
                    ("--status", args.status),
                    ("--result", args.result),
                )
                if value is None
            ]
            if missing:
                parser.error(
                    "para completar un registro hacen falta "
                    + " y ".join(missing)
                )
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    """Build the Spanish argparse parser of the repair CLI (T-16)."""
    parser = argparse.ArgumentParser(
        prog="python -m repair.cli",
        description=(
            "CLI delgada del proceso de reparación de scrapers (spec 004)."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{targets,brief,threshold,quality,record}",
    )

    targets_parser = subparsers.add_parser(
        "targets",
        help="lista los objetivos de reparación del diagnóstico",
        description=(
            "Carga el diagnóstico, valida su vigencia y muestra la cola de "
            "objetivos priorizada: primero las fuentes fallidas y después las "
            "investigaciones de completitud."
        ),
    )
    targets_parser.add_argument(
        "--diagnostic",
        default=None,
        metavar="RUTA",
        help=_DIAGNOSTIC_HELP,
    )
    targets_parser.set_defaults(handler=_cmd_targets)

    brief_parser = subparsers.add_parser(
        "brief",
        help="construye el brief (JSON en inglés) de un objetivo",
        description=(
            "Selecciona un objetivo del diagnóstico por fuente (y campo si la "
            "fuente tiene varios candidatos) y muestra o escribe su brief."
        ),
    )
    brief_parser.add_argument(
        "--source",
        required=True,
        metavar="ID",
        help="id de la fuente del objetivo",
    )
    brief_parser.add_argument(
        "--field",
        default=None,
        metavar="CAMPO",
        help=(
            "campo que elige entre varios candidatos de la misma fuente "
            "(por ejemplo, un secundario frente al primario)"
        ),
    )
    brief_parser.add_argument(
        "--output",
        default=None,
        metavar="RUTA",
        help="escribe el brief en esa ruta en vez de imprimirlo",
    )
    brief_parser.add_argument(
        "--diagnostic",
        default=None,
        metavar="RUTA",
        help=_DIAGNOSTIC_HELP,
    )
    brief_parser.set_defaults(handler=_cmd_brief)

    threshold_parser = subparsers.add_parser(
        "threshold",
        help="muestra el umbral incremental por fuente",
        description=(
            "Lee el historial de recuentos verificados y muestra el umbral "
            "de la próxima reparación de cada fuente (1 sin historial)."
        ),
    )
    threshold_parser.add_argument(
        "--source",
        default=None,
        metavar="ID",
        help="id de una fuente; sin él se listan todas las del historial",
    )
    threshold_parser.add_argument(
        "--history",
        default=None,
        metavar="RUTA",
        help=_HISTORY_HELP,
    )
    threshold_parser.set_defaults(handler=_cmd_threshold)

    quality_parser = subparsers.add_parser(
        "quality",
        help="mide la calidad de la prueba en vivo contra el registro",
        description=(
            "Deserializa el quality_before.json de un registro y aplica la "
            "puerta de calidad al parquet de la prueba en vivo."
        ),
    )
    quality_parser.add_argument(
        "--record",
        required=True,
        metavar="DIRECTORIO",
        help="directorio del registro (repairs/<fecha>-<fuente>/)",
    )
    quality_parser.add_argument(
        "--parquet",
        required=True,
        metavar="RUTA",
        help="ruta del parquet de la prueba en vivo",
    )
    quality_parser.add_argument(
        "--output",
        default=None,
        metavar="RUTA",
        help="escribe el veredicto JSON en esa ruta y lo anuncia",
    )
    quality_parser.set_defaults(handler=_cmd_quality)

    record_parser = subparsers.add_parser(
        "record",
        help="abre o completa el registro de una reparación",
        description=(
            "Con --brief abre un registro en estado planificado a partir del "
            "brief; con --record completa el registro con su estado final."
        ),
    )
    mode = record_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--brief",
        default=None,
        metavar="FICHERO",
        help="abre el registro desde un brief JSON (estado planificado)",
    )
    mode.add_argument(
        "--record",
        default=None,
        metavar="DIRECTORIO",
        help=(
            "completa el registro de ese directorio "
            "(repairs/<fecha>-<fuente>/)"
        ),
    )
    record_parser.add_argument(
        "--on-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="fecha de apertura del registro (solo con --brief)",
    )
    record_parser.add_argument(
        "--status",
        choices=records.FINAL_STATUSES,
        default=None,
        help=(
            "estado final del registro, solo con --record "
            "(probado, descartado o escalado)"
        ),
    )
    record_parser.add_argument(
        "--result",
        default=None,
        metavar="TEXTO",
        help="resultado final en español, solo con --record",
    )
    record_parser.add_argument(
        "--changes",
        default=None,
        metavar="TEXTO",
        help="cambios realizados, solo con --record",
    )
    record_parser.add_argument(
        "--tests",
        default=None,
        metavar="TEXTO",
        help="pruebas de test ejecutadas, solo con --record",
    )
    record_parser.add_argument(
        "--live-test",
        dest="live_test",
        default=None,
        metavar="TEXTO",
        help=(
            "prueba en vivo: alcance, recuento y evidencia, solo con --record"
        ),
    )
    record_parser.add_argument(
        "--verified-offers",
        dest="verified_offers",
        type=int,
        default=None,
        metavar="N",
        help="ofertas verificadas en la prueba en vivo, solo con --record",
    )
    record_parser.add_argument(
        "--quality-after",
        dest="quality_after",
        default=None,
        metavar="FICHERO",
        help=(
            "JSON de la medición de calidad; si se omite, se reutiliza el "
            "quality_after.json del registro"
        ),
    )
    record_parser.set_defaults(handler=_cmd_record)

    return parser


def _cmd_targets(args: argparse.Namespace) -> int:
    """Print the prioritized repair queue of the diagnostic (T-16)."""
    try:
        loaded = targets.load_diagnostic(args.diagnostic)
    except targets.DiagnosticError as exc:
        return _fail(str(exc))
    _print_warnings(loaded.warnings)
    try:
        queue = targets.select_all_targets(loaded)
    except targets.DiagnosticError as exc:
        return _fail(str(exc))
    print(presentation.describe_targets(queue), end="")
    return 0


def _cmd_brief(args: argparse.Namespace) -> int:
    """Build and print or write the brief of one target (T-16)."""
    try:
        loaded = targets.load_diagnostic(args.diagnostic)
        _print_warnings(loaded.warnings)
        target = _single_candidate(
            _candidates(loaded, args.source, args.field),
            source=args.source,
            field=args.field,
        )
        payload = brief.build_brief(target)
        if args.output:
            _write_text_output(
                Path(args.output),
                brief.render_brief(payload) + "\n",
                "el brief",
            )
            print(f"Brief escrito en {args.output}")
        else:
            print(brief.render_brief(payload))
    except targets.DiagnosticError as exc:
        return _fail(str(exc))
    except brief.BriefError as exc:
        return _fail(str(exc))
    except CliInputError as exc:
        return _fail(str(exc))
    return 0


def _cmd_threshold(args: argparse.Namespace) -> int:
    """Print the incremental threshold per source (T-16; RF-10)."""
    loaded = threshold.load_history(args.history)
    _print_warnings(loaded.warnings)
    if args.source:
        print(_threshold_line(loaded.history, args.source))
        return 0
    sources_map = loaded.history.get("sources")
    names = sorted(sources_map) if isinstance(sources_map, dict) else []
    if not names:
        print("No hay fuentes con historial: el umbral de cualquier fuente es 1.")
        return 0
    for name in names:
        print(_threshold_line(loaded.history, name))
    return 0


def _cmd_quality(args: argparse.Namespace) -> int:
    """Measure the live test and apply the quality gate (T-16; RF-16)."""
    before_path = Path(args.record) / "quality_before.json"
    try:
        payload = _read_json(before_path, "el perfil de calidad")
        source, profile = _quality_profile(payload, before_path)
        verdict = quality.evaluate_quality(args.parquet, source, profile)
        print(quality.render_quality_table(verdict), end="")
        if args.output:
            text = (
                json.dumps(
                    quality.verdict_to_dict(verdict),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
            _write_text_output(
                Path(args.output), text, "el veredicto de calidad"
            )
            print(f"Calidad escrita en {args.output}")
    except CliInputError as exc:
        return _fail(str(exc))
    return 0 if verdict.ok else 1


def _cmd_record(args: argparse.Namespace) -> int:
    """Open a record from a brief or complete an existing one (T-16; RF-2)."""
    if args.brief is not None:
        return _open_record(args)
    return _complete_record(args)


def _open_record(args: argparse.Namespace) -> int:
    """Open ``repairs/<fecha>-<fuente>/`` from the given brief (T-13)."""
    try:
        payload = _read_json(Path(args.brief), "el brief")
        record = records.create_record(payload, on_date=args.on_date)
    except CliInputError as exc:
        return _fail(str(exc))
    except records.RecordError as exc:
        return _fail(str(exc))
    print(f"Registro creado en {record.directory}")
    print(f"Rama del fix: {record.branch}")
    print(f"Estado: {records.STATUS_PLANNED}")
    return 0


def _complete_record(args: argparse.Namespace) -> int:
    """Complete a record with its final status (T-14; RF-11)."""
    quality_after: dict | None = None
    try:
        if args.quality_after is not None:
            quality_after = _read_json(
                Path(args.quality_after), "la medición de calidad"
            )
        record = records.complete_record(
            args.record,
            status=args.status,
            result=args.result,
            changes=args.changes,
            tests=args.tests,
            live_test=args.live_test,
            verified_offers=args.verified_offers,
            quality_after=quality_after,
        )
    except CliInputError as exc:
        return _fail(str(exc))
    except records.RecordError as exc:
        return _fail(str(exc))
    print(f"Registro completado en {record.directory}")
    print(f"Estado: {args.status}")
    return 0


def _candidates(
    loaded: targets.LoadedDiagnostic, source: str, field: str | None
) -> tuple[targets.RepairTarget, ...]:
    """Return the queue targets matching ``source`` (and ``field`` when given)."""
    return tuple(
        target
        for target in targets.select_all_targets(loaded)
        if target.source == source
        and (field is None or target.field == field)
    )


def _single_candidate(
    candidates: tuple[targets.RepairTarget, ...],
    *,
    source: str,
    field: str | None,
) -> targets.RepairTarget:
    """Return the only candidate, or raise a Spanish CLI error.

    No candidate is a clear error (the source or field is not in the
    diagnostic queue). Several candidates (a primary target plus its field
    investigations, typically) list their ``role`` and ``field`` and ask for
    ``--field`` when it was not given.
    """
    if not candidates:
        detail = f" y el campo «{field}»" if field else ""
        raise CliInputError(
            f"no hay ningún objetivo para la fuente «{source}»{detail} en el "
            "diagnóstico; usa «targets» para ver los objetivos disponibles"
        )
    if len(candidates) > 1:
        listing = "; ".join(
            f"role={candidate.role}, "
            f"field="
            f"{candidate.field if candidate.field is not None else 'sin campo'}"
            for candidate in candidates
        )
        if field is None:
            hint = "indica «--field» para elegir uno"
        else:
            hint = (
                "el diagnóstico tiene varios objetivos para esa fuente y campo"
            )
        raise CliInputError(
            f"la fuente «{source}» tiene {len(candidates)} objetivos "
            f"candidatos ({listing}); {hint}"
        )
    return candidates[0]


def _threshold_line(history: dict, source: str) -> str:
    """Render one Spanish threshold line for a source (T-16; RF-10)."""
    best = threshold.best_verified(history, source)
    best_text = "sin dato" if best is None else str(best)
    limit = threshold.threshold_for(history, source)
    return f"{source} | mejor verificado: {best_text} | umbral: {limit}"


def _quality_profile(
    payload: dict[str, object], path: Path
) -> tuple[str, tuple[targets.QualityProfile, ...]]:
    """Deserialize the T-13 ``quality_before.json`` contract (input adapter).

    It only reads the stable machine document written by :mod:`repair.records`
    (``source`` plus ``fields[]`` with ``field``, ``required``,
    ``current_pct``, ``target_pct`` and ``is_focus``); no gate rule is applied
    here. A malformed document raises a Spanish :class:`CliInputError`.
    """
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        raise CliInputError(
            f"«{path}» no trae un id de fuente («source») de texto no vacío"
        )
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise CliInputError(
            f"«{path}» no trae la lista de campos («fields») del perfil"
        )
    profile: list[targets.QualityProfile] = []
    for entry in fields:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("field"), str)
            or not entry["field"]
        ):
            raise CliInputError(
                f"«{path}» tiene una fila del perfil sin campo («field»)"
            )
        field = entry["field"]
        required = entry.get("required")
        is_focus = entry.get("is_focus", False)
        if not isinstance(required, bool):
            raise CliInputError(
                f"la fila «{field}» de «{path}» no trae «required» booleano"
            )
        if not isinstance(is_focus, bool):
            raise CliInputError(
                f"la fila «{field}» de «{path}» no trae «is_focus» booleano"
            )
        profile.append(
            targets.QualityProfile(
                field=field,
                required=required,
                current_pct=_optional_pct(entry.get("current_pct")),
                target_pct=_optional_pct(entry.get("target_pct")),
                is_focus=is_focus,
            )
        )
    return source, tuple(profile)


def _read_json(path: Path, label: str) -> dict[str, object]:
    """Read a UTF-8 JSON object, or raise a Spanish CLI error.

    A missing file, an unreadable one, a non-JSON text or a payload that is
    not an object are clear errors of the input; the label names the document
    in the message.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CliInputError(
            f"no se encontró {label} «{path}»: el fichero no existe"
        ) from exc
    except OSError as exc:
        raise CliInputError(
            f"no se pudo leer {label} «{path}»: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise CliInputError(
            f"no se pudo leer {label} «{path}»: no es un fichero UTF-8 válido "
            f"({exc})"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliInputError(
            f"{label} «{path}» no contiene JSON válido: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CliInputError(f"{label} «{path}» no contiene un objeto JSON")
    return payload


def _write_text_output(path: Path, text: str, label: str) -> None:
    """Write UTF-8 with Unix newlines, or raise a Spanish CLI error."""
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise CliInputError(
            f"no se pudo escribir {label} «{path}»: {exc}"
        ) from exc


def _optional_pct(value: object) -> float | None:
    """Return a contract percentage as float, or ``None`` when absent.

    Non-numeric values yield ``None`` (the field was measured but the document
    carries no usable percentage), mirroring the defensive deserialization of
    the core modules; it never invents a number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _print_warnings(warnings: tuple[str, ...]) -> None:
    """Print the Spanish warnings of a loaded document to stderr."""
    for warning in warnings:
        print(f"Aviso: {warning}", file=sys.stderr)


def _fail(message: str) -> int:
    """Print a Spanish domain error to stderr and return the failure code."""
    print(f"Error: {message}", file=sys.stderr)
    return 1


def _force_utf8_output() -> None:
    """Reconfigure stdout/stderr to UTF-8 when the stream supports it.

    The Spanish queue of :mod:`repair.presentation` contains ``→``, which a
    cp1252 Windows console cannot encode. The call is defensive: a stream
    without ``reconfigure`` (or one that rejects the change) is left as is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            continue


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
