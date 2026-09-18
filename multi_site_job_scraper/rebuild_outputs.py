"""Reconstruccion de outputs por sitio tras la sobrescritura del 11/09.

Estrategia rapida (vectorizada):
  1. Lee TODOS los data/<site>/checkpoints/progress_*.csv + output actual
     + filas del sitio en los snapshots respaldados en data/backup_20260911/
  2. Concatena y deduplica por job_id conservando la fila de mayor calidad
     (misma metrica que merge.py).
  3. Pasa el resultado por Store (cuarentena + esquema) y escribe
     output/jobs.csv + jobs.parquet.

Despues ejecuta merge.py (a menos que se pase --no-merge).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from merge import _quality_score  # noqa: E402
from src.core.store import Store  # noqa: E402

SITES = ["irishjobs", "stepstone_nl", "devitjobs", "nvb", "jobs_ch", "glassdoor"]
BACKUP_SNAPSHOTS = [
    PROJECT_ROOT / "data" / "backup_20260911" / "jobs_unified_20260906_110816.parquet",
    PROJECT_ROOT / "data" / "backup_20260911" / "jobs_unified_20260910_030637.parquet",
]


def _read_csv_safe(p: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        return df if len(df) else None
    except Exception as e:
        print(f"  !! ignorando {p.name}: {e}", flush=True)
        return None


def _cp_ts(p: Path) -> pd.Timestamp:
    """Timestamp aproximado del checkpoint a partir de su nombre (o mtime)."""
    stem = p.stem
    try:
        core = stem.split("_", 1)[1]
        return pd.to_datetime(core[:15], format="%Y%m%d_%H%M%S")
    except Exception:
        return pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").tz_localize(None)


def _select_run_finals(cps: list[Path], gap_hours: float = 6.0) -> list[Path]:
    """Los checkpoints son acumulativos dentro de cada run (cada run arranca
    con el store vacio). Basta con el ULTIMO de cada run.
    Solo usa metadatos (timestamp del nombre + tamano): no abre los ficheros,
    porque con miles de ficheros eso es lo que tarda.
    Un run nuevo se detecta por salto temporal > gap_hours."""
    if not cps:
        return []
    infos = []
    for p in cps:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        infos.append((_cp_ts(p), size, p))
    infos.sort(key=lambda t: (t[0], t[1], t[2].name))
    selected: list[Path] = []
    run_start = infos[0][0]
    best = infos[0]
    for ts, size, p in infos[1:]:
        if (ts - run_start).total_seconds() > gap_hours * 3600:
            selected.append(best[2])
            run_start = ts
        best = (ts, size, p)
    selected.append(best[2])
    return selected


def rebuild_site(site: str) -> int:
    print(f"[{site}] inicio", flush=True)
    out_csv = PROJECT_ROOT / "data" / site / "output" / "jobs.csv"
    out_parquet = PROJECT_ROOT / "data" / site / "output" / "jobs.parquet"
    cp_dir = PROJECT_ROOT / "data" / site / "checkpoints"

    frames = []
    cps = sorted(cp_dir.glob("progress_*.csv"))
    finals = _select_run_finals(cps)
    print(f"[{site}] {len(cps)} checkpoints -> {len(finals)} finales de run seleccionados", flush=True)
    for cp in finals:
        df = _read_csv_safe(cp)
        if df is not None:
            frames.append(df)

    if out_csv.exists():
        df = _read_csv_safe(out_csv)
        if df is not None:
            frames.append(df)

    for snap in BACKUP_SNAPSHOTS:
        if not snap.exists():
            continue
        try:
            sdf = pd.read_parquet(snap)
        except Exception:
            continue
        if "site" not in sdf.columns:
            continue
        sub = sdf[sdf["site"] == site].copy()
        if len(sub) == 0:
            continue
        if "skills" in sub.columns:
            sub["skills"] = sub["skills"].map(
                lambda v: "|".join(v) if isinstance(v, (list, tuple)) and len(v) else "")
        frames.append(sub.astype(str))

    if not frames:
        print(f"[{site}] sin fuentes; output intacto", flush=True)
        return 0

    all_df = pd.concat(frames, ignore_index=True)
    before = len(all_df)
    all_df["_quality"] = all_df.apply(_quality_score, axis=1)
    all_df = (all_df.sort_values("_quality", ascending=False)
              .drop_duplicates(subset="job_id", keep="first")
              .drop(columns=["_quality"]))
    print(f"[{site}] {len(cps)} checkpoints + output + snapshots -> "
          f"{len(all_df)} ofertas unicas (de {before} filas)", flush=True)

    combined_csv = Path(tempfile.mkdtemp()) / f"{site}_combined.csv"
    all_df.to_csv(combined_csv, index=False, encoding="utf-8-sig")
    store = Store(out_csv, out_parquet, cp_dir)
    store.load_from_csv(combined_csv)
    store.write_all()
    combined_csv.unlink()
    print(f"[{site}] output reconstruido: {out_csv} ({len(store)} ofertas)", flush=True)
    return len(store)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-merge", action="store_true")
    args = ap.parse_args()

    total = 0
    for site in SITES:
        total += rebuild_site(site)
    print(f"TOTAL reconstruido: {total} ofertas", flush=True)

    if not args.no_merge:
        print("--- Ejecutando merge.py ---", flush=True)
        r = subprocess.run([sys.executable, str(PROJECT_ROOT / "merge.py")])
        return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
