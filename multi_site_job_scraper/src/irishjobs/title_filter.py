"""Filtro de relevancia por titulo (IrishJobs / StepStone NL).

El portal devuelve ofertas cuya busqueda coincide pero cuyo titulo no es
de datos (p.ej. 'Operations Analyst', 'EHR Application Support Analyst').
Una oferta solo se guarda si su titulo contiene literalmente alguna de las
frases del mundo data derivadas de los roles configurados, mas variantes
explicitas (ai/ml engineer).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

log = logging.getLogger(__name__)

EXTRA_TITLE_PHRASES = [
    "ai engineer",
    "ml engineer",
]

_SEP_RE = re.compile(r"[\s\-_/·()\[\].,:;!?&+|]+")


def normalize_title(title: str) -> str:
    """Minusculas y separadores convertidos a espacio para comparar frases."""
    return _SEP_RE.sub(" ", (title or "").strip()).lower()


def phrases_from_roles(roles: Iterable[str]) -> list[str]:
    """Frases a buscar en el titulo: roles configurados + variantes extra."""
    phrases = {normalize_title(r) for r in roles if normalize_title(r)}
    phrases.update(EXTRA_TITLE_PHRASES)
    return sorted(phrases)


def is_relevant_title(title: str, phrases: Iterable[str]) -> bool:
    """True si el titulo contiene literalmente alguna de las frases."""
    norm = normalize_title(title)
    if not norm:
        return False
    return any(p in norm for p in phrases)


# --- Modo ampliado (StepStone NL) -----------------------------------------
# StepStone NL ha dejado de devolver titulos que contengan literalmente el rol:
# busca por texto completo y sirve resultados genericos (p.ej. "Data Analyst"
# devuelve "Warehouse Technician" o "Buyer"). En vez de exigir la frase exacta,
# se acepta cualquier titulo con una senal clara de datos, excluyendo falsos
# positivos evidentes.
_BROAD_SIGNAL = re.compile(
    r"\bdata\b|\banalytics?\b|\banalist[ae]?\b|business intelligence|"
    r"\binsights?\b|machine learning|\bml\b|\bai\b|power\s*bi|"
    r"\betl\b|\bsql\b|data\s*science|data\s*warehouse|"
    r"databricks|snowflake|datastage",
    re.IGNORECASE,
)
_BROAD_EXCLUDE = re.compile(
    r"data\s*entry|data\s*protection|data\s*governance|data\s*privacy|"
    r"warehouse\s+(technician|employee|worker|operative)|"
    r"\bhr\b|human resources|\bbuyer\b|inkoper|account executive|"
    r"sales representative|marketing|customer service|recruiter|"
    r"talent acquisition|lab services",
    re.IGNORECASE,
)


def is_broad_data_title(title: str) -> bool:
    """True si el titulo tiene senal de datos y no es un falso positivo claro."""
    norm = normalize_title(title)
    if not norm:
        return False
    if _BROAD_EXCLUDE.search(norm):
        return False
    return bool(_BROAD_SIGNAL.search(norm))


def filter_cards(cards: list[dict], phrases: Iterable[str],
                 broad: bool = False) -> tuple[list[dict], int]:
    """Devuelve (tarjetas que pasan el filtro, numero de descartadas).

    `broad=True` usa el modo ampliado (StepStone NL); `broad=False` exige que
    el titulo contenga literalmente una de las frases de rol (IrishJobs).
    """
    if not cards:
        return cards, 0
    if broad:
        kept = [c for c in cards if is_broad_data_title(c.get("title", ""))]
        return kept, len(cards) - len(kept)
    if not phrases:
        return cards, 0
    kept = [c for c in cards if is_relevant_title(c.get("title", ""), phrases)]
    return kept, len(cards) - len(kept)