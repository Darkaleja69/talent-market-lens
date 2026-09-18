#!/usr/bin/env python3
"""repair_misaligned.py <fichero.csv> [opciones]

Detecta y repara filas desplazadas (shift) en los outputs de los scrapers.

Causa tipica: cuando una fila pierde un campo (p.ej. company_name vacio o
con saltos de linea sin escapar), todos los valores posteriores se escriben
una columna a la izquierda y los datos "se comen" columnas.

Metodo:
  1. Lee el fichero como strings (dtype=str, keep_default_na=False).
  2. Para cada fila, prueba todos los desplazamientos (por defecto -3..+3).
  3. Puntua cada desplazamiento con validadores por columna (URL, fecha,
     numerico, rating, id, booleano, texto).
  4. Se queda con el desplazamiento con mayor puntuacion; si es distinto de 0,
     repara la fila. Si hay empate, no toca nada (conservador).
  5. Escribe <fichero>_repaired.csv sin tocar el original, con columnas de
     auditoria (_repair_shift, _repair_score, _repair_conflict).

Exit code: 0 siempre que termine; los ficheros con filas reparadas se listan.

Ejemplos:
  python repair_misaligned.py data/multi_site/output/jobs.csv
  python repair_misaligned.py data/multi_site/output/jobs.csv --detect-only
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

URL_RE = re.compile(r"^https?://\S+$")
RATING_RE = re.compile(r"^\d[,.]\d{1,2}$")
NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
SHORT_ID_RE = re.compile(r"^[\w.\-/]{1,80}$")
BOOL_VALUES = {"true", "false", "yes", "no", "1", "0"}


def _v_url(v: str) -> bool:
    return bool(URL_RE.match(v.strip()))


def _v_company_link(v: str) -> bool:
    v = v.strip()
    return bool(v) and (bool(URL_RE.match(v)) or v.startswith("/"))


def _v_rating(v: str) -> bool:
    return bool(RATING_RE.match(v.strip()))


def _v_date(v: str) -> bool:
    v = v.strip()
    if not v:
        return False
    try:
        pd.to_datetime(v)
        return True
    except Exception:
        return False


def _v_num(v: str) -> bool:
    return bool(NUM_RE.match(v.strip().replace("€", "").replace("$", "").replace("£", "").strip()))


def _v_id(v: str) -> bool:
    return bool(SHORT_ID_RE.match(v.strip()))


def _v_bool(v: str) -> bool:
    return v.strip().lower() in BOOL_VALUES


def _v_text(v: str) -> bool:
    return bool(v.strip()) and not URL_RE.match(v.strip())


# Configuracion por scraper: orden de columnas real del CSV + validador por
# columna (solo las columnas "ancla" puntuan; el resto es neutral).
SITES = {
    "multi_site": {
        "columns": [
            "job_id", "job_url", "title", "company_name", "location_raw",
            "location_city", "location_region", "location_country",
            "posted_datetime", "posted_relative", "is_new", "company_url",
            "company_industry", "company_size", "work_mode", "employment_type",
            "experience_level", "salary_raw", "salary_min", "salary_max",
            "salary_currency", "salary_period", "num_applicants",
            "description_full", "description_snippet", "role_summary",
            "company_description", "responsibilities", "requirements",
            "benefits", "skills", "site", "country", "workload_pct",
            "salary_disclosed", "search_role", "search_city", "source",
            "scraped_at",
        ],
        "validators": {
            "job_id": _v_id, "job_url": _v_url, "title": _v_text,
            "company_name": _v_text, "location_raw": _v_text,
            "location_city": _v_text, "location_region": _v_text,
            "location_country": _v_text, "posted_datetime": _v_date,
            "posted_relative": _v_text, "is_new": _v_bool,
            "company_url": _v_url, "salary_min": _v_num, "salary_max": _v_num,
            "num_applicants": _v_num, "workload_pct": _v_num,
            "salary_disclosed": _v_bool, "scraped_at": _v_date,
        },
    },
    "indeed": {
        "columns": [
            "job_key", "viewjob_url", "apply_url", "title", "company",
            "company_id_encrypted", "company_rating", "company_review_count",
            "company_overview_link", "location", "city", "state", "country",
            "salary_min", "salary_max", "salary_type", "salary_text",
            "is_remote", "job_type", "snippet", "description_html",
            "description_text", "posted_relative", "posted_date", "benefits",
            "contract_type", "schedule", "workplace_type", "is_sponsored",
            "city_query", "search_term", "page", "scraped_at",
        ],
        "validators": {
            "job_key": _v_id, "viewjob_url": _v_url, "apply_url": _v_url,
            "title": _v_text, "company": _v_text, "company_rating": _v_rating,
            "company_review_count": _v_num, "company_overview_link": _v_company_link,
            "location": _v_text, "city": _v_text, "state": _v_text,
            "country": _v_text, "salary_min": _v_num, "salary_max": _v_num,
            "is_remote": _v_bool, "posted_date": _v_date,
            "is_sponsored": _v_bool, "page": _v_num, "scraped_at": _v_date,
        },
    },
    "linkedin": {
        "columns": [
            "job_id", "job_url", "title", "company_name", "location_raw",
            "location_city", "location_region", "location_country",
            "posted_datetime", "posted_relative", "is_new", "company_url",
            "company_industry", "company_size", "work_mode", "employment_type",
            "experience_level", "salary_raw", "salary_min", "salary_max",
            "salary_currency", "salary_period", "num_applicants",
            "description_full", "role_summary", "company_description",
            "responsibilities", "requirements", "benefits", "skills",
            "search_role", "search_city", "source", "scraped_at",
        ],
        "validators": {
            "job_id": _v_id, "job_url": _v_url, "title": _v_text,
            "company_name": _v_text, "location_raw": _v_text,
            "location_city": _v_text, "location_region": _v_text,
            "location_country": _v_text, "posted_datetime": _v_date,
            "posted_relative": _v_text, "is_new": _v_bool,
            "company_url": _v_url, "salary_min": _v_num, "salary_max": _v_num,
            "num_applicants": _v_num, "scraped_at": _v_date,
        },
    },
    "infojobs": {
        "columns": [
            "id_oferta", "titulo", "empresa", "ciudad", "provincia", "pais",
            "fecha_publicacion", "categoria", "salario_raw", "salario_min",
            "salario_max", "moneda", "periodo", "jornada", "tipo_contrato",
            "experiencia_min", "modalidad", "descripcion_snippet", "url_oferta",
            "fecha_scraped", "fuente", "ciudad_buscada", "keyword_buscada",
            "pagina",
        ],
        "validators": {
            "id_oferta": _v_id, "titulo": _v_text, "empresa": _v_text,
            "ciudad": _v_text, "provincia": _v_text, "pais": _v_text,
            "fecha_publicacion": _v_date, "salario_min": _v_num,
            "salario_max": _v_num, "url_oferta": _v_url,
            "fecha_scraped": _v_date, "pagina": _v_num,
        },
    },
}


def detect_site(columns: list[str]) -> tuple[str | None, dict]:
    """Detecta que config usar comparando las columnas del fichero."""
    cols_set = set(columns)
    best, best_overlap = None, -1
    for name, cfg in SITES.items():
        overlap = len(cols_set.intersection(cfg["columns"]))
        if overlap > best_overlap:
            best, best_overlap = name, overlap
    return (best, SITES[best]) if best else (None, {})


def repair_row(values: list[str], col_order: list[str],
               validators: dict, max_shift: int) -> tuple[int, int, list[str]]:
    """Devuelve (shift, score, fila_reparada). shift 0 = sin cambios."""
    n = len(values)
    best_score, best_shift, best_row = -1, 0, list(values)
    for k in range(-max_shift, max_shift + 1):
        row = [""] * n
        for i, v in enumerate(values):
            j = i + k
            if 0 <= j < n:
                row[j] = v
        score = 0
        for name, v in zip(col_order, row):
            fn = validators.get(name)
            if v and v.strip() and fn and fn(v):
                score += 1
        if score > best_score:
            best_score, best_shift, best_row = score, k, row
    return best_shift, best_score, best_row


def repair_file(path: Path, detect_only: bool = False, max_shift: int = 3,
                min_score_delta: int = 1) -> int:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    n = len(df)
    if n == 0:
        print(f"{path.name}: vacio, sin nada que reparar")
        return 0

    site, cfg = detect_site(list(df.columns))
    if not site:
        print(f"{path.name}: no se reconoce el esquema (columnas: "
              f"{list(df.columns)[:5]}...) - se omite")
        return 0

    col_order, validators = cfg["columns"], cfg["validators"]
    if list(df.columns) != col_order:
        print(f"{path.name}: aviso: las columnas del fichero no coinciden con "
              f"el esquema de {site}; se usa el orden del fichero y los "
              f"validadores por nombre de columna")
        col_order = list(df.columns)

    rows = df.values.tolist()
    results = []
    repaired = 0
    conflicts = 0
    for r in rows:
        values = ["" if pd.isna(v) else str(v) for v in r]
        shift, score, new_row = repair_row(values, col_order, validators, max_shift)
        base_score = sum(
            1 for name, v in zip(col_order, values)
            if v and v.strip() and validators.get(name) and validators[name](v)
        )
        conflict = shift != 0 and score - base_score < min_score_delta
        if conflict:
            conflicts += 1
            results.append(values + ["", str(score), "conflict"])
            continue
        if shift != 0:
            repaired += 1
        results.append(new_row + [str(shift), str(score), "ok" if shift else ""])

    out = pd.DataFrame(results, columns=col_order + ["_repair_shift", "_repair_score", "_repair_conflict"])
    print(f"{path.name} [{site}]: {n} filas, {repaired} reparadas, "
          f"{conflicts} ambiguas (sin tocar)")

    if detect_only:
        out = out[out["_repair_shift"].astype(str).ne("0")]
        if out.empty:
            print("  detect-only: ninguna fila desplazada")
            return 0
        out.to_csv(path.with_name(path.stem + "_flagged.csv"), index=False,
                   encoding="utf-8-sig")
        print(f"  detect-only: {len(out)} filas -> {path.stem}_flagged.csv")
        return 0

    if repaired == 0 and conflicts == 0:
        return 0
    out.to_csv(path.with_name(path.stem + "_repaired.csv"), index=False,
               encoding="utf-8-sig")
    print(f"  -> {path.stem}_repaired.csv")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="repair_misaligned")
    ap.add_argument("fichero", type=Path)
    ap.add_argument("--detect-only", action="store_true",
                    help="solo detectar y exportar las filas desplazadas")
    ap.add_argument("--max-shift", type=int, default=3,
                    help="desplazamiento maximo a probar (default: 3)")
    ap.add_argument("--min-score-delta", type=int, default=1,
                    help="mejora minima de puntuacion para reparar (default: 1)")
    args = ap.parse_args()

    if not args.fichero.exists():
        print(f"no existe: {args.fichero}", file=sys.stderr)
        return 1
    return repair_file(args.fichero, detect_only=args.detect_only,
                       max_shift=args.max_shift,
                       min_score_delta=args.min_score_delta)


if __name__ == "__main__":
    sys.exit(main())