"""Incremental offer threshold and verified-count history of repairs (RF-10).

The live test of a repair must obtain more offers than the greatest count
already verified for that source: ``threshold = max(1, best_verified + 1)``
(plan §9). The rule is pure and stateless (:func:`incremental_threshold`);
the history I/O of T-09 keeps that per-source best in
``repairs/history.json`` (plan §3.4).

History format (English, stable, machine-readable)::

    {
      "schema_version": 1,
      "sources": {
        "indeed": {"best_verified_offers": 2}
      }
    }

T-14 may enrich a source entry with extra keys (for example the last result
or a quality delta) without breaking this format: :func:`load_history` ignores
unknown keys and returns them untouched, and :func:`save_history` writes them
back. Reading never fails on a missing or corrupt file: it yields an empty
history plus a Spanish, code-inspectable warning (same philosophy as T-01).

Design rules:

- The pure rule has no I/O: :func:`incremental_threshold` only computes.
- ``None`` and ``0`` are equivalent: without history the threshold is the
  minimum (1), because a repair is never proven by zero offers.
- The threshold never decreases: :func:`record_verified` keeps the maximum of
  the stored and the new count, and sources are independent.
- :func:`save_history` writes atomically (temporary file in the same directory
  plus ``os.replace``) and never leaves an orphan temporary behind.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# A repair is never proven by zero offers: without history the threshold is 1.
MINIMUM_THRESHOLD = 1

# Only the history schema this process knows how to interpret is accepted.
HISTORY_SCHEMA_VERSION = 1

# scrapers-pipeline/repair/threshold.py -> parents[2] is the repository root.
DEFAULT_HISTORY_PATH = (
    Path(__file__).resolve().parents[2] / "repairs" / "history.json"
)


@dataclass(frozen=True)
class LoadedHistory:
    """A history plus the Spanish warnings detected while loading it.

    ``history`` is the parsed payload (or an empty history when the file was
    missing or corrupt) and ``warnings`` holds code-inspectable notes; loading
    never fails because of them.
    """

    path: Path
    history: dict
    warnings: tuple[str, ...]


def incremental_threshold(best_verified: int | None) -> int:
    """Return the threshold of the next repair of a source (RF-10).

    ``best_verified`` is the greatest offer count given as good in previous
    repairs of that source, or ``None`` when there is no history. The result is
    ``max(1, best_verified + 1)``: the next live test must beat the previous
    best by at least one offer, and the first repair needs at least one offer.
    The function is pure, deterministic and per-source: the caller keeps the
    best count of each source (``repairs/history.json``, T-09), so the
    threshold never decreases.
    """
    if best_verified is None:
        return MINIMUM_THRESHOLD
    return max(MINIMUM_THRESHOLD, best_verified + 1)


def empty_history() -> dict:
    """Return a new empty history with the current schema version."""
    return {"schema_version": HISTORY_SCHEMA_VERSION, "sources": {}}


def load_history(path: str | Path | None = None) -> LoadedHistory:
    """Load the verified-count history, tolerating a missing or corrupt file.

    ``path`` defaults to ``repairs/history.json`` anchored to the repository
    root, so it works from the repository root and from ``scrapers-pipeline/``
    alike. A missing file yields an empty history without warnings. An
    unreadable, non-JSON or structurally invalid file yields an empty history
    plus one Spanish warning in :attr:`LoadedHistory.warnings`; the file is
    never modified and no error is raised.
    """
    history_path = Path(path) if path is not None else DEFAULT_HISTORY_PATH
    try:
        text = history_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LoadedHistory(
            path=history_path, history=empty_history(), warnings=()
        )
    except (OSError, UnicodeDecodeError) as exc:
        return LoadedHistory(
            path=history_path,
            history=empty_history(),
            warnings=(
                f"no se pudo leer el historial «{history_path}»: {exc}; se "
                "usa un historial vacío",
            ),
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return LoadedHistory(
            path=history_path,
            history=empty_history(),
            warnings=(
                f"el historial «{history_path}» no contiene JSON válido: "
                f"{exc}; se usa un historial vacío",
            ),
        )
    problem = _history_problem(payload)
    if problem is not None:
        return LoadedHistory(
            path=history_path,
            history=empty_history(),
            warnings=(
                f"el historial «{history_path}» no es válido ({problem}); se "
                "usa un historial vacío",
            ),
        )
    return LoadedHistory(path=history_path, history=payload, warnings=())


def best_verified(history: dict, source: str) -> int | None:
    """Return the greatest verified count of a source, or ``None``.

    The lookup is defensive: a history without the source, or with a malformed
    entry, simply has no verified count.
    """
    sources_map = history.get("sources")
    if not isinstance(sources_map, dict):
        return None
    entry = sources_map.get(source)
    if not isinstance(entry, dict):
        return None
    value = entry.get("best_verified_offers")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def threshold_for(history: dict, source: str) -> int:
    """Return the incremental threshold of a source from the history (RF-10)."""
    return incremental_threshold(best_verified(history, source))


def record_verified(history: dict, source: str, offers: int) -> dict:
    """Return a new history with the verified count of a source raised (RF-10).

    The stored ``best_verified_offers`` becomes ``max(current, offers)``, so it
    never decreases; other sources are independent and their entries are not
    mutated, and unknown keys of the updated entry (T-14 enrichment) are kept.
    The input history is never modified.
    """
    if not isinstance(source, str) or not source:
        raise ValueError("el id de la fuente debe ser texto no vacío")
    if isinstance(offers, bool) or not isinstance(offers, int) or offers < 0:
        raise ValueError(
            f"el recuento verificado de «{source}» debe ser un entero ≥ 0, "
            f"no {offers!r}"
        )
    current = best_verified(history, source)
    best = offers if current is None else max(current, offers)
    new_history = dict(history)
    sources_map = new_history.get("sources")
    new_sources = dict(sources_map) if isinstance(sources_map, dict) else {}
    entry = new_sources.get(source)
    new_entry = dict(entry) if isinstance(entry, dict) else {}
    new_entry["best_verified_offers"] = best
    new_sources[source] = new_entry
    new_history["sources"] = new_sources
    return new_history


def save_history(
    history: dict, path: str | Path | None = None
) -> Path:
    """Write the history atomically in UTF-8 and return the written path.

    The directory is created when missing and the JSON is written to a
    temporary file in the same directory (so ``os.replace`` stays atomic) with
    stable English formatting: sorted keys, two-space indentation and a final
    newline. A failed write removes its temporary file.
    """
    history_path = Path(path) if path is not None else DEFAULT_HISTORY_PATH
    history_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(history, ensure_ascii=False, sort_keys=True, indent=2)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=history_path.parent,
            prefix=f"{history_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text + "\n")
        os.replace(temp_path, history_path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return history_path


def _history_problem(payload: object) -> str | None:
    """Return the Spanish reason why a payload is not a valid history, or None.

    Unknown root keys and unknown entry keys are allowed (T-14 enriches the
    entries); the schema version, the ``sources`` map and each
    ``best_verified_offers`` (integer ≥ 0) are required.
    """
    if not isinstance(payload, dict):
        return "la raíz no es un objeto JSON"
    version = payload.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != HISTORY_SCHEMA_VERSION
    ):
        return (
            f"schema_version {version!r} distinto de {HISTORY_SCHEMA_VERSION}"
        )
    sources_map = payload.get("sources")
    if not isinstance(sources_map, dict):
        return "«sources» no es un objeto JSON"
    for source_id, entry in sources_map.items():
        if not isinstance(source_id, str) or not source_id:
            return "hay una fuente sin id de texto"
        if not isinstance(entry, dict):
            return f"la entrada de «{source_id}» no es un objeto JSON"
        best = entry.get("best_verified_offers")
        if isinstance(best, bool) or not isinstance(best, int) or best < 0:
            return (
                f"«best_verified_offers» de «{source_id}» no es un entero ≥ 0"
            )
    return None
