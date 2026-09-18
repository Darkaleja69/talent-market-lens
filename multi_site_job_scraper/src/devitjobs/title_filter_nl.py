"""Filtro de títulos para DevITJobs (reutiliza la logica generica de IrishJobs).

Mantener el filtro en un solo sitio evita divergencias entre portales.
"""
from __future__ import annotations

from src.irishjobs.title_filter import (  # noqa: F401
    is_broad_data_title,
    is_relevant_title,
    normalize_title,
    phrases_from_roles,
)

__all__ = [
    "is_broad_data_title",
    "is_relevant_title",
    "normalize_title",
    "phrases_from_roles",
]
