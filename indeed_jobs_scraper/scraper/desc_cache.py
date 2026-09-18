"""Cache persistente de descripciones y condiciones por job_key.

Motivo: la SERP de Indeed solo trae descripcion completa del job auto-abierto
(o ninguna). Obtenerla para cada oferta requiere clicar el panel derecho o
visitar /viewjob, que es la parte mas cara en riesgo anti-bot. Como las mismas
ofertas reaparecen noche tras noche, guardamos lo ya obtenido y lo reutilizamos
sin volver a clicar.

Flujo:
  1. Al enriquecer, `apply_cache_to_offers` rellena las ofertas que ya tienen
     entrada en cache (0 clics).
  2. Solo las que siguen sin descripcion son candidatas a clic, con un tope
     (max_enrich) para no disparar heuristicas anti-bot.
  3. Tras cada ciudad, `update_cache_from_offers` guarda lo nuevo y
     `save_cache` lo persiste.

El fichero es JSON (legible, inspeccionable) con escritura atomica para no
corromperse si el run se corta a mitad.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import JobOffer

log = logging.getLogger(__name__)

CACHE_VERSION = 1
MIN_DESCRIPTION_LEN = 50

# Campos de condiciones que se cosechan del panel derecho y merece la pena cachear.
_CACHED_FIELDS = (
    "description_html",
    "description_text",
    "benefits",
    "contract_type",
    "schedule",
    "workplace_type",
)


def _has_description(offer: "JobOffer") -> bool:
    return bool(offer.description_html and len(offer.description_html) > MIN_DESCRIPTION_LEN)


def load_cache(path: Path) -> dict[str, dict]:
    """Carga el cache de descripciones. Devuelve dict job_key -> entrada.

    Si el fichero no existe o esta corrupto, arranca vacio (no rompe el run).
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("desc-cache corrupto (%s). Arrancando vacio.", e)
        return {}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return {}
    log.info("desc-cache cargado: %d descripciones", len(jobs))
    return jobs


def save_cache(path: Path, jobs: dict[str, dict]) -> None:
    """Persiste el cache de forma atomica (tmp + replace)."""
    payload = {
        "version": CACHE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(jobs),
        "jobs": jobs,
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log.warning("no se pudo guardar desc-cache: %s", e)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def apply_cache_to_offers(offers: list["JobOffer"], cache: dict[str, dict]) -> int:
    """Rellena las ofertas que ya estan en cache (sin clics). Devuelve cuantas relleno.

    Solo escribe campos que la oferta aun no tiene, para no pisar datos frescos
    de la SERP con datos antiguos del cache.
    """
    if not cache:
        return 0
    filled = 0
    for offer in offers:
        jk = getattr(offer, "job_key", "")
        if not jk or _has_description(offer):
            continue
        entry = cache.get(jk)
        if not isinstance(entry, dict):
            continue
        if not (entry.get("description_html") or ""):
            continue
        for field_name in _CACHED_FIELDS:
            value = entry.get(field_name) or ""
            if value and not getattr(offer, field_name, ""):
                setattr(offer, field_name, value)
        if not offer.description_text and offer.description_html:
            from .parser import _clean_html

            offer.description_text = _clean_html(offer.description_html)
        if offer.description_text:
            offer.snippet = offer.description_text[:500]
        filled += 1
    if filled:
        log.info("desc-cache: %d ofertas rellenadas sin clics", filled)
    return filled


def update_cache_from_offers(cache: dict[str, dict], offers: list["JobOffer"]) -> int:
    """Guarda/actualiza entradas de las ofertas con descripcion. Devuelve cambios."""
    changed = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for offer in offers:
        jk = getattr(offer, "job_key", "")
        if not jk or not _has_description(offer):
            continue
        entry = {field_name: (getattr(offer, field_name, "") or "") for field_name in _CACHED_FIELDS}
        existing = cache.get(jk)
        if not isinstance(existing, dict) or any(
            existing.get(field_name) != entry[field_name] for field_name in _CACHED_FIELDS
        ):
            entry["cached_at"] = now
            cache[jk] = entry
            changed += 1
    if changed:
        log.info("desc-cache: %d entradas nuevas/actualizadas", changed)
    return changed


def cache_size(cache: dict[str, dict]) -> int:
    return len(cache)
