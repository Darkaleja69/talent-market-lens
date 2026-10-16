"""Recomputa los campos derivados del snapshot local (sin red) — T-33.

Entrada: ``python -m tools.recompute_derived`` desde ``linkedin_jobs_scraper/``.

La extraccion de salario y la inferencia de modalidad mejoradas en T-33 solo
se aplican a las filas nuevas; las ya almacenadas conservan lo que se calculo
con las reglas viejas. Esta herramienta recarga ``data/output/jobs.csv`` en el
Store, recomputa solo las ofertas que pueden mejorar (un valor valido nunca se
pisa) y reescribe el snapshot:

- ``salary_raw`` vacio o sin ningun digito: se recalcula desde la descripcion;
  si no hay importe, se limpia el valor invalido.
- ``work_mode`` vacio: se recalcula con la inferencia multilingue.
- Las filas sin descripcion y la columna ``skills`` no se tocan.

Antes de escribir se copian ``jobs.csv`` y ``jobs.parquet`` a
``backup-<ts>-jobs.csv`` / ``backup-<ts>-jobs.parquet`` junto a ellos. El
informe (espanol) imprime los contadores antes/despues y el recuento total de
filas, que no debe cambiar.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.models import JobOffer
from src.parse_detail import _extract_salary_from_description, _infer_work_mode
from src.store import Store

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_salary_valid(salary_raw: str) -> bool:
    """True si el texto de salario almacenado tiene al menos un digito."""
    return bool(salary_raw) and any(ch.isdigit() for ch in salary_raw)


def recompute_offer_derived(offer: JobOffer) -> dict[str, bool]:
    """Aplica la regla "solo si falta o es invalido" a una oferta (pura).

    Muta la oferta en sitio (como el parser del scraper) y devuelve que
    cambio: ``salary_filled`` (se encontro importe), ``salary_cleaned`` (se
    limpió un valor invalido sin importe) y ``work_mode_filled``. Las ofertas
    sin descripcion y el campo ``skills`` nunca se tocan.
    """
    report = {
        "salary_filled": False,
        "salary_cleaned": False,
        "work_mode_filled": False,
    }
    if not (offer.description_full or "").strip():
        return report
    if not is_salary_valid(offer.salary_raw or ""):
        raw, smin, smax, currency, period = _extract_salary_from_description(
            offer.description_full
        )
        if raw:
            offer.salary_raw = raw
            offer.salary_min = smin
            offer.salary_max = smax
            offer.salary_currency = currency
            offer.salary_period = period
            report["salary_filled"] = True
        elif offer.salary_raw:
            # Valor invalido sin importe: se limpia en vez de conservar basura.
            offer.salary_raw = ""
            offer.salary_min = None
            offer.salary_max = None
            offer.salary_currency = ""
            offer.salary_period = ""
            report["salary_cleaned"] = True
    if not (offer.work_mode or "").strip():
        inferred = _infer_work_mode(offer.description_full)
        if inferred:
            offer.work_mode = inferred
            report["work_mode_filled"] = True
    return report


def _output_config(root: Path) -> dict[str, Any]:
    """Config del Store con las rutas de ``config.yaml`` resueltas a ``root``."""
    config_path = root / "config.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise SystemExit(f"Error: no se pudo leer «{config_path}»: {exc}")
    output = config.get("output") or {}
    resolved = {
        key: str(root / value) if not Path(str(value)).is_absolute() else str(value)
        for key, value in output.items()
        if isinstance(value, str) and value
    }
    return {"output": resolved}


def _backup_snapshot(path: Path, stamp: str) -> Path | None:
    """Copia ``path`` a ``backup-<stamp>-<nombre>``; None si no existe."""
    if not path.exists():
        return None
    backup = path.with_name(f"backup-{stamp}-{path.name}")
    shutil.copy2(path, backup)
    return backup


def _snapshot_counters(store: Store) -> dict[str, int]:
    """Contadores del snapshot actual (antes/despues del recomputo)."""
    offers = store.all()
    return {
        "total": len(offers),
        "salary_valid": sum(
            1 for o in offers if is_salary_valid(o.salary_raw or "")
        ),
        "salary_invalid": sum(
            1 for o in offers
            if (o.salary_raw or "") and not is_salary_valid(o.salary_raw)
        ),
        "salary_empty": sum(
            1 for o in offers if not (o.salary_raw or "").strip()
        ),
        "work_mode": sum(1 for o in offers if (o.work_mode or "").strip()),
    }


def _print_report(before: dict[str, int], after: dict[str, int],
                  changes: dict[str, int], backups: list[Path],
                  written_total: int | None) -> None:
    """Imprime el informe antes/despues del recomputo (espanol)."""
    print("== Recomputo de campos derivados (sin red) ==")
    print(f"Filas totales antes:        {before['total']}")
    print(f"salary_raw valido antes:    {before['salary_valid']}")
    print(f"salary_raw invalido antes:  {before['salary_invalid']}")
    print(f"salary_raw vacio antes:     {before['salary_empty']}")
    print(f"work_mode no vacio antes:   {before['work_mode']}")
    print("--")
    print(f"salary completado:          {changes['salary_filled']}")
    print(f"salary invalido limpiado:   {changes['salary_cleaned']}")
    print(f"work_mode recuperado:       {changes['work_mode_filled']}")
    print("--")
    print(f"salary_raw valido despues:  {after['salary_valid']}")
    print(f"salary_raw invalido despues:{after['salary_invalid']}")
    print(f"salary_raw vacio despues:   {after['salary_empty']}")
    print(f"work_mode no vacio despues: {after['work_mode']}")
    if backups:
        print("Copias de seguridad: " + ", ".join(str(p) for p in backups))
    if written_total is not None:
        estado = "sin cambios" if written_total == before["total"] else "CAMBIO"
        print(f"Filas totales despues:      {written_total} ({estado})")


def main() -> int:
    """Recarga, recomputa y reescribe el snapshot. Devuelve el exit code."""
    root = PROJECT_ROOT
    config = _output_config(root)
    csv_path = Path(config["output"]["csv"])
    parquet_path = Path(config["output"]["parquet"])

    store = Store(config)
    loaded = store.load_from_csv()
    if loaded == 0:
        print("Nada que recomputar: el snapshot no tiene filas.")
        return 0

    before = _snapshot_counters(store)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = [p for p in (_backup_snapshot(csv_path, stamp),
                           _backup_snapshot(parquet_path, stamp)) if p]

    changes = {"salary_filled": 0, "salary_cleaned": 0,
               "work_mode_filled": 0}
    for offer in store.all():
        result = recompute_offer_derived(offer)
        for key in changes:
            changes[key] += int(result[key])

    after = _snapshot_counters(store)
    print("Antes de escribir:")
    _print_report(before, after, changes, backups, written_total=None)

    store.write_all()
    try:
        written_total = len(
            pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"Error: no se pudo releer «{csv_path}»: {exc}")
        return 1
    print("Despues de escribir:")
    _print_report(before, after, changes, backups, written_total)
    if written_total != before["total"]:
        print("Error: el recuento total de filas cambio; revisa las copias "
              "de seguridad.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
