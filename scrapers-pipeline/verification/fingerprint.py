"""Comparability fingerprint of the effective sources and searches (RF-7).

Two runs are only comparable when they cover the *same* sources and the *same*
searches per region (RF-7, plan section 4.3 and technical decision 6). This
module turns the effective configuration of a run into a small, deterministic
fingerprint that is stored inside the manifest the pipeline already writes
(``_manifests/<scraper>/<stamp>.json``); it does not create a parallel history
store (constitution #5).

The fingerprint is pure data derived from:

- the source ids that actually produced a valid Parquet, and
- the distinct search/region/city combinations present in that Parquet (read
  from the scraper's own trace columns, so no scraper change is needed).

By construction it contains only sources and search dimensions, so it never
carries credentials. :func:`is_credential_free` is an explicit second line of
defence: :func:`build_fingerprint` refuses to serialise a fingerprint whose
text still looks like a secret (``sig=``, SAS token, password, ...).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from verification import sources

# Bump when the canonical payload layout changes; old manifests keep their own
# value and are simply not treated as comparable unless the hash matches.
FINGERPRINT_SCHEMA_VERSION = 1

# Text fragments that betray a secret. They are matched case-insensitively on
# word boundaries so ordinary search terms ("data engineer", "Data Analyst")
# pass while tokens/SAS signatures do not.
_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsig\s*=", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"\baccountkey\b", re.IGNORECASE),
    re.compile(r"\bsharedaccesssignature\b", re.IGNORECASE),
    re.compile(r"\bsas\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class SearchDimension:
    """One effective search: which source searched what, where."""

    source: str
    search: str
    region: str = ""
    city: str = ""


def _texts(value: object) -> Iterable[str]:
    """Yield every piece of text contained in ``value`` (nested too)."""
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _texts(item)
    elif isinstance(value, Iterable):
        for item in value:
            yield from _texts(item)
    else:
        yield str(value)


def is_credential_free(value: object) -> bool:
    """Return True when no text in ``value`` looks like a secret.

    Accepts a plain string or any nested structure of mappings/sequences. The
    check is intentionally conservative: a value containing ``sig=``, a SAS
    token or the words ``token``/``password``/``secret``/``accountkey`` is
    rejected, while ordinary search terms pass.
    """
    for text in _texts(value):
        if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
            return False
    return True


def _clean(value: object) -> str:
    """Return a trimmed string (empty for ``None``)."""
    if value is None:
        return ""
    return str(value).strip()


def _dimension_key(dimension: SearchDimension) -> tuple[str, str, str, str]:
    return (
        _clean(dimension.source),
        _clean(dimension.search),
        _clean(dimension.region),
        _clean(dimension.city),
    )


def build_fingerprint(
    sources: Iterable[str],
    searches: Iterable[SearchDimension],
    *,
    schema_version: int = FINGERPRINT_SCHEMA_VERSION,
) -> dict:
    """Build the deterministic comparability fingerprint of a run (RF-7).

    Sources and search dimensions are trimmed and deduplicated, then sorted
    into a stable order (sources alphabetically, dimensions by
    ``(source, search, region, city)``). The ``hash`` is the sha256 of that
    canonical payload, so the same configuration always yields the same
    fingerprint regardless of input order.

    Raises :class:`ValueError` when the resulting fingerprint still looks like
    it contains credentials; a run must never publish such a manifest.
    """
    source_ids = sorted({clean for clean in (_clean(s) for s in sources) if clean})
    unique_dimensions = {_dimension_key(dimension) for dimension in searches}
    ordered_dimensions = sorted(unique_dimensions)
    search_dicts = [
        {"source": source, "search": search, "region": region, "city": city}
        for source, search, region, city in ordered_dimensions
    ]
    canonical = {"sources": source_ids, "searches": search_dicts}
    if not is_credential_free(canonical):
        raise ValueError(
            "La huella de fuentes/búsquedas contiene un valor con posible credencial."
        )
    payload = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digest = "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()
    return {
        "schema_version": schema_version,
        "sources": source_ids,
        "searches": search_dicts,
        "hash": digest,
    }


def fingerprints_match(a: dict | None, b: dict | None) -> bool:
    """Return True only when both fingerprints exist and hash the same (RF-7).

    ``None`` or a mapping without a non-empty ``hash`` is never comparable.
    """
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return False
    hash_a = a.get("hash")
    hash_b = b.get("hash")
    if not isinstance(hash_a, str) or not isinstance(hash_b, str):
        return False
    if not hash_a or not hash_b:
        return False
    return hash_a == hash_b


def _cell(value: object) -> str:
    """Return a trimmed cell value, mapping ``None`` to an empty string."""
    return "" if value is None else str(value).strip()


def search_dimensions_from_table(
    table: object,
    source_id: str,
    *,
    site: str | None = None,
) -> tuple[SearchDimension, ...]:
    """Extract the distinct search dimensions present in a Parquet table.

    Uses the trace-column map of ``sources`` for ``source_id`` (T-32). For
    Multi-site, the unified table carries a ``site`` column: pass ``site`` to
    select one portal's rows, or omit it to group every portal by its own
    ``site`` value. The resulting ``SearchDimension.source`` is the portal id
    for Multi-site and ``source_id`` otherwise.

    A table without the trace columns (or an unknown source) yields ``()`` and
    never raises, so an older Parquet cannot break the pipeline.
    """
    mapping = sources.search_trace_columns(source_id)
    if not mapping:
        return ()
    column_names = set(getattr(table, "schema").names)
    search_column = mapping.get("search")
    if search_column is None or search_column not in column_names:
        return ()

    search_values = table.column(search_column).to_pylist()
    region_column = mapping.get("region")
    city_column = mapping.get("city")
    site_column = mapping.get("site")
    region_values = (
        table.column(region_column).to_pylist()
        if region_column in column_names
        else None
    )
    city_values = (
        table.column(city_column).to_pylist() if city_column in column_names else None
    )
    site_values = (
        table.column(site_column).to_pylist() if site_column in column_names else None
    )

    wanted_site = _cell(site) if site is not None else None
    dimensions: set[SearchDimension] = set()
    for index in range(table.num_rows):
        row_site = _cell(site_values[index]) if site_values is not None else ""
        if wanted_site is not None and site_values is not None and row_site != wanted_site:
            continue
        source_value = (
            wanted_site if wanted_site is not None else (row_site or source_id)
        )
        dimensions.add(
            SearchDimension(
                source=source_value,
                search=_cell(search_values[index]),
                region=(
                    _cell(region_values[index]) if region_values is not None else ""
                ),
                city=(_cell(city_values[index]) if city_values is not None else ""),
            )
        )
    return tuple(
        sorted(dimensions, key=lambda d: (d.source, d.search, d.region, d.city))
    )
