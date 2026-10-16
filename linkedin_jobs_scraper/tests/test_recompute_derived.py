"""Tests offline de la herramienta de recomputo de derivados (T-33).

Verifican:
 - `is_salary_valid`: valido solo si el texto tiene algun digito.
 - `recompute_offer_derived`: regla "solo si falta o es invalido" por oferta
   (salario rellenado/limpiado, work_mode rellenado) sin tocar valores validos,
   filas sin descripcion ni `skills`.
 - `main`: flujo completo sobre un snapshot temporal, con copias de seguridad,
   informe antes/despues y recuento de filas invariante.
"""
from __future__ import annotations

import re
from pathlib import Path

import tools.recompute_derived as tool
from src.models import JobOffer, now_utc_iso
from src.store import Store

_DESC = "Buscamos un perfil de datos. " + ("x" * 120)


def _offer(job_id: str, *, description: str = _DESC, salary_raw: str = "",
           salary_min=None, salary_max=None, salary_currency: str = "",
           salary_period: str = "", work_mode: str = "",
           skills: list[str] | None = None) -> JobOffer:
    return JobOffer(
        job_id=job_id,
        job_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        title=f"Title {job_id}",
        company_name="ACME",
        description_full=description,
        salary_raw=salary_raw,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        work_mode=work_mode,
        skills=list(skills or []),
        search_role="Data Analyst",
        search_city="Madrid",
        source="local",
        scraped_at=now_utc_iso(),
    )


# ---------- is_salary_valid ----------

def test_is_salary_valid_requires_a_digit():
    assert tool.is_salary_valid("40.000-55.000 euros/año")
    assert tool.is_salary_valid("€40.000")
    assert tool.is_salary_valid("17.00")
    assert not tool.is_salary_valid("")
    assert not tool.is_salary_valid(", libra")
    assert not tool.is_salary_valid(". €")
    assert not tool.is_salary_valid("., Euro")


# ---------- recompute_offer_derived ----------

def test_valid_salary_is_never_overwritten():
    offer = _offer(
        "1", description="Salary: 30.000-40.000 euros/año. " + _DESC,
        salary_raw="40.000-55.000 euros/año", salary_min=40000.0,
        salary_max=55000.0, salary_currency="EUR", salary_period="year",
        work_mode="Hibrido")
    report = tool.recompute_offer_derived(offer)
    assert report == {"salary_filled": False, "salary_cleaned": False,
                      "work_mode_filled": False}
    assert offer.salary_raw == "40.000-55.000 euros/año"
    assert offer.salary_min == 40000.0
    assert offer.work_mode == "Hibrido"


def test_invalid_salary_without_amount_is_cleaned():
    offer = _offer("1", salary_raw=", libra", salary_min=1.0,
                   salary_currency="GBP", work_mode="Remoto")
    report = tool.recompute_offer_derived(offer)
    assert report == {"salary_filled": False, "salary_cleaned": True,
                      "work_mode_filled": False}
    assert offer.salary_raw == ""
    assert offer.salary_min is None and offer.salary_max is None
    assert offer.salary_currency == "" and offer.salary_period == ""


def test_empty_salary_is_filled_from_description():
    offer = _offer("1", description="Salario: 30.000-40.000 euros/año. " + _DESC)
    report = tool.recompute_offer_derived(offer)
    assert report["salary_filled"] is True
    assert offer.salary_raw
    assert offer.salary_min == 30000.0
    assert offer.salary_max == 40000.0
    assert offer.salary_currency == "EUR"
    assert offer.salary_period == "year"


def test_work_mode_is_filled_with_multilingual_inference():
    offer = _offer("1", description="Hybride werken is mogelijk. " + _DESC,
                   salary_raw="€30.000")
    report = tool.recompute_offer_derived(offer)
    assert report["work_mode_filled"] is True
    assert offer.work_mode == "Hibrido"


def test_offer_without_description_and_skills_are_untouched():
    offer = _offer("1", description="", salary_raw=", libra", work_mode="",
                   skills=["Python"])
    report = tool.recompute_offer_derived(offer)
    assert report == {"salary_filled": False, "salary_cleaned": False,
                      "work_mode_filled": False}
    assert offer.salary_raw == ", libra"
    assert offer.work_mode == ""
    assert offer.skills == ["Python"]


def test_empty_work_mode_without_signal_stays_empty():
    offer = _offer("1", description=_DESC, work_mode="")
    report = tool.recompute_offer_derived(offer)
    assert report["work_mode_filled"] is False
    assert offer.work_mode == ""


# ---------- main (flujo completo) ----------

def _seed_snapshot(root: Path) -> dict:
    output_dir = root / "data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "output:\n"
        '  csv: "data/output/jobs.csv"\n'
        '  parquet: "data/output/jobs.parquet"\n'
        '  checkpoint_dir: "data/checkpoints"\n',
        encoding="utf-8",
    )
    config = {"output": {
        "csv": str(output_dir / "jobs.csv"),
        "parquet": str(output_dir / "jobs.parquet"),
        "checkpoint_dir": str(root / "data" / "checkpoints"),
    }}
    seed = Store(config)
    seed.add(_offer("1", description="Salario: 30.000-40.000 euros/año. " + _DESC))
    seed.add(_offer("2", salary_raw=", libra", work_mode="Remoto"))
    seed.add(_offer("3", description="", salary_raw=", libra"))
    seed.write_csv()
    seed.write_parquet()
    return config


def test_main_recomputes_backs_up_and_keeps_row_count(tmp_path, monkeypatch,
                                                     capsys):
    config = _seed_snapshot(tmp_path)
    monkeypatch.setattr(tool, "PROJECT_ROOT", tmp_path)

    code = tool.main()

    assert code == 0
    out = capsys.readouterr().out
    assert re.search(r"^salary completado:\s+1$", out, re.MULTILINE)
    assert re.search(r"^salary invalido limpiado:\s+1$", out, re.MULTILINE)
    assert re.search(r"^work_mode recuperado:\s+0$", out, re.MULTILINE)
    assert "(sin cambios)" in out

    output_dir = tmp_path / "data" / "output"
    backups = sorted(output_dir.glob("backup-*-jobs.*"))
    assert {b.suffix for b in backups} == {".csv", ".parquet"}

    reloaded = Store(config)
    assert reloaded.load_from_csv() == 3
    assert reloaded.get("1").salary_min == 30000.0
    assert reloaded.get("2").salary_raw == ""  # basura limpiada
    assert reloaded.get("3").salary_raw == ", libra"  # sin descripcion: intacta


def test_main_without_rows_is_a_noop(tmp_path, monkeypatch, capsys):
    output_dir = tmp_path / "data" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(
        "output:\n"
        '  csv: "data/output/jobs.csv"\n'
        '  parquet: "data/output/jobs.parquet"\n'
        '  checkpoint_dir: "data/checkpoints"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "PROJECT_ROOT", tmp_path)

    assert tool.main() == 0
    assert "Nada que recomputar" in capsys.readouterr().out
    assert not list(output_dir.glob("backup-*"))
