from datetime import datetime
from pathlib import Path
import re
import pandas as pd
from deltalake.writer import write_deltalake
from scraper.config import DATA_DIR, DELTA_DIR
from scraper.models import Offer

_RATING_OR_URL = re.compile(r"^\d[,.]\d{1,2}$|^https?://")


def _quarantine_incoherent(df: pd.DataFrame) -> pd.DataFrame:
    """Descarta filas incoherentes (rating/URL como empresa, id_oferta vacio,
    URL rota) y las guarda en data/quality/ para auditoria."""
    if df is None or len(df) == 0:
        return df

    def _coherent(r) -> bool:
        oid = str(r.get("id_oferta", "") or "").strip()
        if not oid:
            return False
        empresa = str(r.get("empresa", "") or "").strip()
        if empresa and _RATING_OR_URL.match(empresa):
            return False
        url = str(r.get("url_oferta", "") or "").strip()
        if url and not re.match(r"^https?://", url):
            return False
        return True

    mask = df.apply(_coherent, axis=1)
    if mask.all():
        return df
    bad, good = df[~mask], df[mask]
    qdir = DATA_DIR / "quality"
    qdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    qpath = qdir / f"bad_rows_{ts}.csv"
    bad.to_csv(qpath, index=False, encoding="utf-8-sig")
    print(f"[coherence] {len(bad)} fila(s) incoherente(s) -> cuarentena en {qpath}")
    return good


def _to_dataframe(offers: list[Offer]) -> pd.DataFrame:
    rows = [o.model_dump() for o in offers]
    df = _quarantine_incoherent(pd.DataFrame(rows))
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype("string[python]")
    return df


def save_excel(offers: list[Offer], timestamp: str | None = None) -> Path:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    df = _to_dataframe(offers)
    path = DATA_DIR / f"offers_{ts}.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path


def save_parquet(offers: list[Offer], timestamp: str | None = None) -> Path:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    df = _to_dataframe(offers)
    path = DATA_DIR / f"offers_{ts}.parquet"
    df.to_parquet(path, index=False, engine="pyarrow",
                  coerce_timestamps="us", allow_truncated_timestamps=True)
    return path


def save_delta(offers: list[Offer]) -> Path:
    df = _to_dataframe(offers)
    write_deltalake(
        str(DELTA_DIR),
        df,
        mode="overwrite",
    )
    return DELTA_DIR


def save_all(offers: list[Offer]) -> dict[str, Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "excel": save_excel(offers, ts),
        "parquet": save_parquet(offers, ts),
        "delta": save_delta(offers),
    }
