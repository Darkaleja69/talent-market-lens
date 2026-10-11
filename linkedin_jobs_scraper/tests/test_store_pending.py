"""Tests de los helpers de `detail_pending` del Store (T-33).

Verifican (sin navegador, sin red):
 - add/get/increment/remove funcionan y conservan first_seen.
 - checkpoint persiste `detail_pending` en state.json.
 - la carga es retrocompatible (state.json sin la clave o con entradas rotas).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.models import JobOffer, now_utc_iso
from src.store import Store


def _make_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "output": {
            "csv": str(tmp_path / "jobs.csv"),
            "parquet": str(tmp_path / "jobs.parquet"),
            "checkpoint_dir": str(tmp_path / "checkpoints"),
        }
    }


def _make_offer(job_id: str) -> JobOffer:
    return JobOffer(
        job_id=job_id,
        job_url=f"https://linkedin.com/jobs/view/{job_id}/",
        title=f"Title {job_id}",
        company_name="ACME",
        search_role="Data Analyst",
        search_city="Madrid",
        source="local",
        scraped_at=now_utc_iso(),
    )


def _state_path(config: dict[str, Any]) -> Path:
    return Path(config["output"]["checkpoint_dir"]) / "state.json"


def test_pending_helpers_roundtrip(tmp_path):
    store = Store(_make_config(tmp_path))
    assert store.detail_pending == {}
    assert store.pending_detail_get("100") is None

    entry = store.pending_detail_add("100")
    assert entry["attempts"] == 0
    assert entry["first_seen"]

    store.pending_detail_increment("100")
    store.pending_detail_increment("100")
    assert store.pending_detail_get("100")["attempts"] == 2

    # Volver a marcar como pendiente conserva first_seen y attempts.
    first_seen = store.pending_detail_get("100")["first_seen"]
    store.pending_detail_add("100", first_seen="otra")
    assert store.pending_detail_get("100")["first_seen"] == first_seen
    assert store.pending_detail_get("100")["attempts"] == 2

    assert store.pending_detail_remove("100") is True
    assert store.pending_detail_remove("100") is False
    assert store.pending_detail_get("100") is None


def test_pending_increment_creates_missing_entry(tmp_path):
    store = Store(_make_config(tmp_path))
    entry = store.pending_detail_increment("999")
    assert entry["attempts"] == 1
    assert entry["first_seen"]
    assert store.pending_detail_get("999")["attempts"] == 1


def test_pending_add_honors_explicit_first_seen(tmp_path):
    store = Store(_make_config(tmp_path))
    entry = store.pending_detail_add("100", first_seen="2026-10-11T00:00:00+00:00")
    assert entry["first_seen"] == "2026-10-11T00:00:00+00:00"


def test_checkpoint_persists_detail_pending_and_load_reads_it(tmp_path):
    config = _make_config(tmp_path)
    store = Store(config)
    store.add(_make_offer("100"))
    store.pending_detail_add("100")
    store.pending_detail_increment("100")
    store.checkpoint(tag="test")

    state = json.loads(_state_path(config).read_text(encoding="utf-8"))
    assert state["detail_pending"]["100"]["attempts"] == 1

    reloaded = Store(config)
    assert reloaded.pending_detail_get("100")["attempts"] == 1
    assert reloaded.pending_detail_get("100")["first_seen"]


def test_load_state_without_detail_pending_is_compatible(tmp_path):
    """Un state.json previo (sin la clave) arranca con el mapa vacio."""
    config = _make_config(tmp_path)
    state_p = _state_path(config)
    state_p.parent.mkdir(parents=True, exist_ok=True)
    state_p.write_text(json.dumps({
        "total": 1,
        "job_ids": ["111"],
        "updated_at": "2026-10-01T00:00:00+00:00",
    }), encoding="utf-8")

    store = Store(config)
    assert store.already_processed("111")
    assert store.detail_pending == {}
    assert store.pending_detail_get("111") is None


def test_load_state_normalizes_broken_pending_entries(tmp_path):
    """Entradas corruptas o de tipos raros no rompen la carga del estado."""
    config = _make_config(tmp_path)
    state_p = _state_path(config)
    state_p.parent.mkdir(parents=True, exist_ok=True)
    state_p.write_text(json.dumps({
        "total": 3,
        "job_ids": ["1", "2", "3"],
        "detail_pending": {
            "1": {"attempts": "2", "first_seen": 5},
            "2": "roto",
            "3": {"attempts": -1},
        },
    }), encoding="utf-8")

    store = Store(config)
    assert store.pending_detail_get("1") == {"first_seen": "", "attempts": 2}
    assert store.pending_detail_get("2") == {"first_seen": "", "attempts": 0}
    assert store.pending_detail_get("3") == {"first_seen": "", "attempts": 0}
