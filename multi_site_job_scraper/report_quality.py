"""Metricas de calidad de los outputs de los scrapers.

Uso:
    python report_quality.py [--csv PATH]

Lee data/*/output/jobs.csv y reporta por sitio:
  - ofertas totales y unicas (por job_id)
  - % de completitud de campos clave
  - anomalias conocidas (empresa = rating, ubicacion = titulo, URLs rotas)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
_IS_RATING = re.compile(r"^\d[,.]\d$")
_IS_HTTP = re.compile(r"^https?://")


def quality_report(path: Path) -> dict:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    n = len(df)
    if n == 0:
        return {"total": 0, "unique": 0}

    uniq = df["job_id"].nunique() if "job_id" in df else n

    def pct(col: str) -> float:
        if col not in df:
            return 0.0
        return round(100 * df[col].astype(str).str.strip().ne("").sum() / n, 1)

    anomalies = {
        "company_es_rating": 0,
        "location_igual_titulo": 0,
        "sin_job_id": 0,
        "sin_url": 0,
        "url_rota": 0,
    }
    if "company_name" in df:
        anomalies["company_es_rating"] = int(
            df["company_name"].astype(str).str.strip().str.match(_IS_RATING).sum())
    if "location_raw" in df and "title" in df:
        anomalies["location_igual_titulo"] = int(
            (df["location_raw"].astype(str).str.strip() ==
             df["title"].astype(str).str.strip()).sum())
    if "job_id" in df:
        anomalies["sin_job_id"] = int(df["job_id"].astype(str).str.strip().eq("").sum())
    if "job_url" in df:
        urls = df["job_url"].astype(str).str.strip()
        non_empty = urls.ne("")
        anomalies["sin_url"] = int(non_empty.eq(False).sum())
        anomalies["url_rota"] = int(
            urls[non_empty].apply(
                lambda u: not bool(_IS_HTTP.match(u))).sum())

    return {
        "total": n,
        "unique": uniq,
        "pct_title": pct("title"),
        "pct_company": pct("company_name"),
        "pct_location": pct("location_raw"),
        "pct_posted": pct("posted_relative"),
        "pct_url": pct("job_url"),
        "pct_desc_full": pct("description_full"),
        "pct_salary": pct("salary_raw"),
        "pct_work_mode": pct("work_mode"),
        "pct_emp_type": pct("employment_type"),
        **anomalies,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="report_quality")
    ap.add_argument("--csv", type=Path, default=None,
                    help="CSV concreto (default: todos los data/*/output/jobs.csv)")
    args = ap.parse_args()

    paths = [args.csv] if args.csv else sorted(
        PROJECT_ROOT.glob("data/*/output/jobs.csv"))
    paths = [p for p in paths if "merged" not in str(p)]
    if not paths:
        print("No se encontraron outputs.")
        return 1

    headers = ["sitio", "total", "unicos", "%titulo", "%empresa", "%ubic",
               "%fecha", "%url", "%desc", "%salario", "%modo", "%tipo",
               "emp=rating", "ubic=titulo", "sin_id", "url_rota"]
    print(" | ".join(f"{h:>12}" for h in headers))
    for p in paths:
        site = p.parent.parent.name
        try:
            r = quality_report(p)
        except Exception as e:
            print(f"{site}: ERROR {e}")
            continue
        if r["total"] == 0:
            print(f"{site:>12} | 0 | sin datos")
            continue
        row = [
            site, r["total"], r["unique"], r["pct_title"], r["pct_company"],
            r["pct_location"], r["pct_posted"], r["pct_url"], r["pct_desc_full"],
            r["pct_salary"], r["pct_work_mode"], r["pct_emp_type"],
            r["company_es_rating"], r["location_igual_titulo"],
            r["sin_job_id"], r["url_rota"],
        ]
        print(" | ".join(f"{str(v):>12}" for v in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())