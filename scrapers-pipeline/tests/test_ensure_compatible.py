"""Tests de la cuarentena por filas en ``ensure_compatible.py`` (T-15).

Un fichero con filas incoherentes no debe perder las validas: las malas se
escriben a ``<quarantine>/bad_rows_<stem>_<ts>.csv`` y el Parquet limpio se sube.
Un fichero estructuralmente invalido (ilegible, sin columnas obligatorias, vacio)
o con **todas** las filas incoherentes se sigue rechazando entero.

Se ejecuta ``ensure_compatible.py`` como subproceso sobre un staging temporal
con Parquet de PyArrow (ya justificado por el plan, seccion 6.3); sin red,
AzCopy ni credenciales.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
ENSURE_SCRIPT = PIPELINE_DIR / "ensure_compatible.py"

REQUIRED = "job_id,job_url,title,company_name,scraped_at"


def _good_row(index: int) -> dict:
    """Fila coherente con la regla dura de multi_site.

    ``salary_min`` alterna valor y nulo: un nulo es legal y no puede contar
    como incoherencia (los null de Arrow llegan como ``None``, no como NaN).
    """
    return {
        "job_id": "job-%03d" % index,
        "job_url": "https://example.test/jobs/%d" % index,
        "title": "Data engineer %d" % index,
        "company_name": "Company %d" % index,
        "scraped_at": "2026-11-04T10:00:00",
        "salary_min": 40000.0 if index % 2 else None,
        "site": "glassdoor",
        "search_role": "data engineer",
        "search_city": "Madrid",
    }


def _bad_row(index: int, *, motive: str = "rating") -> dict:
    """Fila incoherente: un rating colado en company_name o una URL rota."""
    row = _good_row(index)
    if motive == "rating":
        row["company_name"] = "4,5"
    else:
        row["job_url"] = "not-a-url"
    return row


def _write_parquet(path: Path, rows: list[dict]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path, compression="snappy")


def _run_validator(
    stage: Path, manifest: Path, quarantine: Path | None
) -> subprocess.CompletedProcess:
    argv = [
        sys.executable,
        str(ENSURE_SCRIPT),
        str(stage),
        "--manifest",
        str(manifest),
        "--required-cols",
        REQUIRED,
        "--coherence",
        "multi_site",
        "--fingerprint-source",
        "multi_site",
    ]
    if quarantine is not None:
        argv += ["--quarantine-dir", str(quarantine)]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bad_rows_csvs(quarantine: Path) -> list[Path]:
    return sorted(quarantine.glob("bad_rows_*.csv")) if quarantine.exists() else []


def _read_csv(path: Path) -> list[dict]:
    # El CSV se escribe con BOM (utf-8-sig) para que Excel lo abra bien.
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# Filas incoherentes: se separan y el fichero limpio se sube
# --------------------------------------------------------------------------


def test_incoherent_rows_are_separated_and_clean_parquet_is_published(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    quarantine = tmp_path / "quarantine" / "multi_site"
    rows = [_good_row(i) for i in range(5)]
    rows += [_bad_row(5, motive="rating"), _bad_row(6, motive="url")]
    _write_parquet(stage / "jobs_unified.parquet", rows)
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine)

    # El fichero queda subible: la separacion no es un rechazo.
    assert result.returncode == 0, result.stderr
    assert "PARCIAL" in result.stdout
    assert "2 separada(s)" in result.stdout

    # Parquet limpio, mismo nombre y esquema, solo con las filas validas.
    clean_path = stage / "jobs_unified.parquet"
    clean = pq.read_table(clean_path)
    assert clean.num_rows == 5
    assert clean.column_names == list(rows[0])
    assert clean.column("job_id").to_pylist() == ["job-%03d" % i for i in range(5)]

    # CSV de filas malas en la cuarentena, con los dos motivos.
    csvs = _bad_rows_csvs(quarantine)
    assert len(csvs) == 1
    bad = _read_csv(csvs[0])
    assert [row["job_id"] for row in bad] == ["job-005", "job-006"]
    assert bad[0]["company_name"] == "4,5"
    assert bad[1]["job_url"] == "not-a-url"
    # El rechazado entero no se toca: aqui no hay parquet en cuarentena.
    assert list(quarantine.glob("*.parquet")) == []

    # El manifest distingue el estado y lleva el recuento de filas separadas;
    # `rows` describe el fichero que se sube, no el original.
    payload = _read_manifest(manifest_path)
    assert payload["total_files"] == 1
    assert payload["bad_files"] == 0
    entry = payload["files"][0]
    assert entry["status"] == "partial"
    assert entry["rows"] == 5
    assert entry["dropped_rows"] == 2
    assert entry["error"] is None
    assert entry["sha256"] == hashlib.sha256(clean_path.read_bytes()).hexdigest()
    assert entry["bytes"] == clean_path.stat().st_size


def test_two_files_with_bad_rows_get_one_csv_each_in_the_same_run(tmp_path):
    """Dos parquet del mismo staging no comparten CSV aunque caigan en el mismo segundo.

    El nombre incluye el stem del fichero de origen
    (``bad_rows_<stem>_<ts>.csv``): antes solo llevaba el timestamp con
    precision de segundo y el segundo fichero pisaba el CSV del primero,
    perdiendo sus filas en cuarentena.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    quarantine = tmp_path / "quarantine" / "multi_site"
    # Cada fichero: una fila valida y una incoherente.
    _write_parquet(
        stage / "glassdoor.parquet", [_good_row(0), _bad_row(1, motive="rating")]
    )
    _write_parquet(
        stage / "indeed.parquet", [_good_row(2), _bad_row(3, motive="url")]
    )
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine)

    assert result.returncode == 0, result.stderr
    # Un CSV por fichero de origen, identificado por su stem y su timestamp.
    csvs = _bad_rows_csvs(quarantine)
    assert len(csvs) == 2, [p.name for p in csvs]
    for stem, bad_id in (("glassdoor", "job-001"), ("indeed", "job-003")):
        matches = [
            p for p in csvs if re.fullmatch(
                r"bad_rows_%s_\d{8}_\d{6}\.csv" % stem, p.name
            )
        ]
        assert len(matches) == 1, [p.name for p in csvs]
        # Cada CSV conserva la fila mala de SU fichero.
        assert [row["job_id"] for row in _read_csv(matches[0])] == [bad_id]
    # Los dos parquet quedan subibles con solo su fila valida.
    for name in ("glassdoor.parquet", "indeed.parquet"):
        assert pq.read_table(stage / name).num_rows == 1
    # El manifest registra un fichero parcial por parquet, con su recuento.
    payload = _read_manifest(manifest_path)
    assert payload["total_files"] == 2
    assert payload["bad_files"] == 0
    entries = {entry["file"]: entry for entry in payload["files"]}
    assert set(entries) == {"glassdoor.parquet", "indeed.parquet"}
    for entry in entries.values():
        assert entry["status"] == "partial"
        assert entry["rows"] == 1
        assert entry["dropped_rows"] == 1
        assert entry["error"] is None


def test_clean_file_keeps_status_ok_and_writes_no_csv(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    quarantine = tmp_path / "quarantine" / "multi_site"
    _write_parquet(
        stage / "jobs_unified.parquet", [_good_row(i) for i in range(4)]
    )
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine)

    assert result.returncode == 0, result.stderr
    assert "OK (4 filas)" in result.stdout
    assert pq.read_table(stage / "jobs_unified.parquet").num_rows == 4
    assert _bad_rows_csvs(quarantine) == []
    entry = _read_manifest(manifest_path)["files"][0]
    assert entry["status"] == "ok"
    assert entry["rows"] == 4
    assert entry["dropped_rows"] == 0


# --------------------------------------------------------------------------
# Estructuralmente invalido: rechazo entero, como hasta ahora
# --------------------------------------------------------------------------


def _unreadable(directory: Path) -> None:
    (directory / "jobs_unified.parquet").write_bytes(b"no es un parquet")


def _missing_required_column(directory: Path) -> None:
    row = _good_row(0)
    row.pop("company_name")
    _write_parquet(directory / "jobs_unified.parquet", [row])


def _empty(directory: Path) -> None:
    # Parquet valido sin filas: por debajo de --min-rows (default 1).
    _write_parquet(directory / "jobs_unified.parquet", [])


@pytest.mark.parametrize(
    "build", [_unreadable, _missing_required_column, _empty], ids=lambda f: f.__name__
)
def test_structurally_invalid_file_is_rejected_as_a_whole(tmp_path, build):
    stage = tmp_path / "stage"
    stage.mkdir()
    quarantine = tmp_path / "quarantine" / "multi_site"
    build(stage)
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine)

    assert result.returncode == 1
    assert "INVALIDO" in result.stderr
    # Ni parquet ni CSV subibles: el fichero entero se mueve a cuarentena.
    assert list(stage.glob("*.parquet")) == []
    assert [p.name for p in quarantine.glob("*.parquet")] == [
        "jobs_unified.parquet"
    ]
    assert _bad_rows_csvs(quarantine) == []
    payload = _read_manifest(manifest_path)
    assert payload["bad_files"] == 1
    entry = payload["files"][0]
    assert entry["status"] == "bad"
    assert entry["rows"] == 0
    assert entry["dropped_rows"] == 0
    assert entry["error"]


# --------------------------------------------------------------------------
# Todas las filas incoherentes: rechazo entero, nunca un parquet vacio
# --------------------------------------------------------------------------


def test_all_rows_incoherent_rejects_the_file_and_publishes_nothing(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    quarantine = tmp_path / "quarantine" / "multi_site"
    _write_parquet(
        stage / "jobs_unified.parquet",
        [_bad_row(i) for i in range(3)],
    )
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine)

    assert result.returncode == 1
    assert "todas las filas" in result.stderr
    # Nada que subir: ni un parquet vacio en el staging.
    assert list(stage.glob("*.parquet")) == []
    assert [p.name for p in quarantine.glob("*.parquet")] == [
        "jobs_unified.parquet"
    ]
    # Las filas malas quedan registradas en el CSV (no se pierden).
    csvs = _bad_rows_csvs(quarantine)
    assert len(csvs) == 1
    assert len(_read_csv(csvs[0])) == 3
    entry = _read_manifest(manifest_path)["files"][0]
    assert entry["status"] == "bad"
    assert entry["rows"] == 0
    assert entry["dropped_rows"] == 0
    assert "todas las filas" in entry["error"]


def test_without_quarantine_dir_the_whole_file_is_rejected(tmp_path):
    """Sin destino no se pueden conservar las malas: fail-safe, rechazo entero."""
    stage = tmp_path / "stage"
    stage.mkdir()
    rows = [_good_row(0), _bad_row(1)]
    _write_parquet(stage / "jobs_unified.parquet", rows)
    manifest_path = tmp_path / "manifest.json"

    result = _run_validator(stage, manifest_path, quarantine=None)

    assert result.returncode == 1
    assert "sin --quarantine-dir" in result.stderr
    # El fichero sigue intacto: no se ha recortado ninguna fila.
    assert pq.read_table(stage / "jobs_unified.parquet").num_rows == 2
    entry = _read_manifest(manifest_path)["files"][0]
    assert entry["status"] == "bad"
    assert entry["rows"] == 0
