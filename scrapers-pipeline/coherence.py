"""Validacion de coherencia de filas para outputs de scrapers.

Capa de defensa comun del pipeline: cada fila debe cumplir los patrones
esperados por columna (URL en columnas de URL, fecha parseable en columnas
de fecha, company_name sin ratings/URLs, job_id no vacio, etc.).

Si una fila no es coherente, NO debe escribirse tal cual al CSV/Parquet/Delta
(o subirse a ADLS): se marca, se pone en cuarentena y se informa, para que
un fallo del parser (campo omitido, columnas desplazadas, rating filtrado a
company_name) nunca llegue a Databricks en silencio.

Uso:
    from coherence import SITES, validate_row, quarantine_bad_rows
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

URL_RE = re.compile(r"^https?://\S+$")
RATING_RE = re.compile(r"^\d[,.]\d{1,2}$")
NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
SHORT_ID_RE = re.compile(r"^[\w.\-/]{1,80}$")
BOOL_VALUES = {"true", "false", "yes", "no", "1", "0"}


def _v_url(v): return bool(v) and bool(URL_RE.match(v.strip()))
def _v_company_link(v):
    """Enlace de empresa: URL absoluta o ruta relativa tipo '/cmp/...'."""
    v = v.strip()
    return bool(v) and (bool(URL_RE.match(v)) or v.startswith("/"))
def _v_rating(v): return bool(v) and bool(RATING_RE.match(v.strip()))
def _v_num(v):
    return bool(v) and bool(NUM_RE.match(
        v.strip().replace("€", "").replace("$", "").replace("£", "").strip()))
PCT_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)?%?$")
def _v_pct(v):
    """Porcentaje opcional o rango (100%, 80-100%, 80 - 100%) para workload_pct."""
    s = v.strip().replace("\u2013", "-").replace("\u2014", "-").replace(" ", "")
    return bool(s) and bool(PCT_RE.match(s))
def _v_id(v): return bool(v) and bool(SHORT_ID_RE.match(v.strip()))
def _v_bool(v): return bool(v) and v.strip().lower() in BOOL_VALUES
def _v_text(v): return bool(v and v.strip()) and not URL_RE.match(v.strip())


def _v_date(v):
    if not v or not v.strip():
        return False
    try:
        pd.to_datetime(v)
        return True
    except Exception:
        return False


# Validadores por columna "ancla". Las columnas no listadas son neutrales:
# no puntuan ni pueden invalidar una fila.
ANCHOR_VALIDATORS: dict[str, object] = {
    "job_id": _v_id, "job_key": _v_id, "id_oferta": _v_id,
    "job_url": _v_url, "viewjob_url": _v_url, "apply_url": _v_url,
    "url_oferta": _v_url,
    "company_url": _v_company_link,
    "company_overview_link": _v_company_link,
    "company": _v_text, "company_name": _v_text, "empresa": _v_text,
    "title": _v_text, "titulo": _v_text,
    "location_raw": _v_text, "location_city": _v_text, "location_region": _v_text,
    "location_country": _v_text, "city": _v_text, "state": _v_text,
    "country": _v_text, "ciudad": _v_text, "provincia": _v_text, "pais": _v_text,
    "posted_datetime": _v_date, "posted_date": _v_date,
    "fecha_publicacion": _v_date, "fecha_scraped": _v_date,
    "scraped_at": _v_date,
    "posted_relative": _v_text, "description_full": _v_text,
    "description_snippet": _v_text, "description_text": _v_text,
    "company_rating": _v_rating, "company_review_count": _v_num,
    "salary_min": _v_num, "salary_max": _v_num, "salary_min_annual": _v_num,
    "salary_max_annual": _v_num, "num_applicants": _v_num,
    "workload_pct": _v_pct, "page": _v_num, "pagina": _v_num,
    "is_new": _v_bool, "is_remote": _v_bool, "is_sponsored": _v_bool,
    "salary_disclosed": _v_bool,
}

# Reglas duras (incumplirlas = fila invalida y cuarentena):
#   (columna, comprobacion) donde comprobacion devuelve True si la fila es VALIDA.
HARD_RULES: dict[str, list[tuple[str, object]]] = {
    "multi_site": [
        ("job_id", lambda v: bool(v.strip())),
        ("job_url", lambda v: not v.strip() or bool(URL_RE.match(v.strip()))),
        ("company_name", lambda v: not v.strip()
         or (not RATING_RE.match(v.strip()) and not URL_RE.match(v.strip()))),
        ("title", lambda v: not v.strip() or not URL_RE.match(v.strip())),
    ],
    "linkedin": [
        ("job_id", lambda v: bool(v.strip())),
        ("job_url", lambda v: not v.strip() or bool(URL_RE.match(v.strip()))),
        ("company_name", lambda v: not v.strip()
         or (not RATING_RE.match(v.strip()) and not URL_RE.match(v.strip()))),
        ("title", lambda v: not v.strip() or not URL_RE.match(v.strip())),
    ],
    "indeed": [
        ("job_key", lambda v: bool(v.strip())),
        ("viewjob_url", lambda v: not v.strip() or bool(URL_RE.match(v.strip()))),
        ("company", lambda v: not v.strip()
         or (not RATING_RE.match(v.strip()) and not URL_RE.match(v.strip()))),
        ("title", lambda v: not v.strip() or not URL_RE.match(v.strip())),
    ],
    "infojobs": [
        ("id_oferta", lambda v: bool(v.strip())),
        ("url_oferta", lambda v: not v.strip() or bool(URL_RE.match(v.strip()))),
        ("empresa", lambda v: not v.strip()
         or (not RATING_RE.match(v.strip()) and not URL_RE.match(v.strip()))),
        ("titulo", lambda v: not v.strip() or not URL_RE.match(v.strip())),
    ],
}


def coherence_issues(row: dict, site: str | None = None) -> list[str]:
    """Devuelve la lista de problemas de coherencia de una fila (dict col->valor).

    - Reglas duras de HARD_RULES[site] (o reglas genericas si site es None).
    - Columnas ancla con patron rompible (URL en columna de texto, texto en
      columna de URL, rating en company_name...).
    """
    issues: list[str] = []

    rules = HARD_RULES.get(site or "", [])
    if not rules:
        rules = [r for rs in HARD_RULES.values() for r in rs]
    for col, check in rules:
        val = str(row.get(col, "") or "")
        if not check(val):
            issues.append(f"{col}='{val[:40]}'")

    for col, val0 in row.items():
        if val0 is None:
            continue
        val = str(val0).strip()
        if not val:
            continue
        fn = ANCHOR_VALIDATORS.get(col)
        if fn is None:
            continue
        if fn in (_v_id, _v_url, _v_bool, _v_rating, _v_num, _v_pct, _v_date):
            if not fn(val):
                issues.append(f"{col} no valida ('{val[:40]}')")
    return issues


def quarantine_bad_rows(df: pd.DataFrame, site: str, out_dir: Path,
                        label: str = "") -> pd.DataFrame:
    """Separa filas incoherentes y las escribe a <out_dir>/bad_rows_<ts>.csv.

    Devuelve el DataFrame SOLO con filas validas. Si no hay problemas,
    devuelve el mismo DataFrame y no escribe nada.
    """
    if df is None or len(df) == 0:
        return df

    bad_mask = df.apply(
        lambda r: bool(coherence_issues(r.to_dict(), site)), axis=1)
    if not bad_mask.any():
        return df

    bad = df[bad_mask]
    good = df[~bad_mask]
    out_dir.mkdir(parents=True, exist_ok=True)
    import datetime as dt
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    label_suffix = f"_{label}" if label else ""
    path = out_dir / f"bad_rows_{ts}{label_suffix}.csv"
    bad.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[coherence] {site}: {len(bad)} fila(s) incoherentes -> cuarentena "
          f"{path}")
    return good