"""Tests del reintento acotado de detalles pendientes de main.py (T-33).

Verifican (sin navegador, sin red):
 - `_pending_window_days`: ventana por `date_filter` de la ciudad (7/30, tope).
 - `_select_pending_for_retry`: descripcion vacia, ventana por ciudad,
   `attempts < 3`, orden mas antiguo primero y presupuesto acotado.
 - `_retry_pending_details`: recuperacion (remove + PROGRESS), fallo
   (increment + WARNING) y respeto del tope de intentos, con `parse_detail`
   monkeypatcheado.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import src.main as main
from src.login import BlockedException
from src.main import (
    _MAX_PENDING_DETAIL_ATTEMPTS,
    _pending_window_days,
    _retry_pending_details,
    _select_pending_for_retry,
)
from src.models import JobOffer
from src.progress import RunProgressCounter
from src.store import Store

_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _offer(job_id: str, *, city: str = "Madrid", description: str = "",
           age_days: float | None = 1.0, scraped_at: str | None = None
           ) -> JobOffer:
    if scraped_at is None and age_days is not None:
        scraped_at = (_NOW - timedelta(days=age_days)).isoformat(timespec="seconds")
    return JobOffer(
        job_id=job_id,
        job_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        title=f"Title {job_id}",
        company_name="ACME",
        description_full=description,
        search_role="Data Analyst",
        search_city=city,
        source="local",
        scraped_at=scraped_at or "",
    )


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "cities": {
            "Madrid": {"text": "Madrid, Spain", "date_filter": "r604800"},
            "Ireland": {"text": "Ireland", "date_filter": "r2592000"},
        },
        "date_filter": "r604800",
    }
    config.update(overrides)
    return config


def _store(tmp_path: Path) -> Store:
    return Store({"output": {
        "csv": str(tmp_path / "jobs.csv"),
        "parquet": str(tmp_path / "jobs.parquet"),
        "checkpoint_dir": str(tmp_path / "checkpoints"),
    }})


# ---------- _pending_window_days ----------

def test_pending_window_days_uses_city_date_filter():
    config = _config()
    assert _pending_window_days(config, "Madrid") == 7
    assert _pending_window_days(config, "Ireland") == 30


def test_pending_window_days_defaults_to_seven_days():
    assert _pending_window_days({"cities": {}}, "Madrid") == 7
    assert _pending_window_days({}, "Madrid") == 7
    config = {"date_filter": "r86400", "cities": {"Madrid": {}}}
    assert _pending_window_days(config, "Madrid") == 7  # filtro no mapeado


# ---------- _select_pending_for_retry ----------

def test_select_pending_uses_city_window_and_orders_oldest_first():
    config = _config()
    madrid_recent = _offer("m1", city="Madrid", age_days=3)
    madrid_old = _offer("m2", city="Madrid", age_days=10)   # fuera de 7 dias
    ireland_old = _offer("i1", city="Ireland", age_days=20)  # dentro de 30

    selected = _select_pending_for_retry(
        [madrid_recent, madrid_old, ireland_old], config, {}, _NOW, 10)

    assert [o.job_id for o in selected] == ["i1", "m1"]


def test_select_pending_respects_budget_with_oldest_first():
    config = _config()
    offers = [
        _offer("recent", age_days=1),
        _offer("oldest", age_days=5),
        _offer("middle", age_days=3),
    ]
    selected = _select_pending_for_retry(offers, config, {}, _NOW, 2)
    assert [o.job_id for o in selected] == ["oldest", "middle"]


def test_select_pending_skips_offers_with_description_and_attempt_cap():
    config = _config()
    with_desc = _offer("done", description="Una descripcion ya guardada.")
    exhausted = _offer("exhausted")
    attempts_map = {
        "exhausted": {"first_seen": "", "attempts": _MAX_PENDING_DETAIL_ATTEMPTS},
        "retryable": {"first_seen": "", "attempts": 1},
    }
    retryable = _offer("retryable")
    selected = _select_pending_for_retry(
        [with_desc, exhausted, retryable], config, attempts_map, _NOW, 10)
    assert [o.job_id for o in selected] == ["retryable"]


def test_select_pending_falls_back_to_first_seen_when_scraped_at_missing():
    config = _config()
    offer = _offer("no-date", scraped_at="")
    attempts_map = {
        "no-date": {
            "first_seen": (_NOW - timedelta(days=2)).isoformat(timespec="seconds"),
            "attempts": 0,
        },
    }
    selected = _select_pending_for_retry([offer], config, attempts_map, _NOW, 10)
    assert [o.job_id for o in selected] == ["no-date"]


def test_select_pending_skips_undated_offers_and_zero_budget():
    config = _config()
    undated = _offer("undated", scraped_at="")
    assert _select_pending_for_retry([undated], config, {}, _NOW, 10) == []
    dated = _offer("dated", age_days=1)
    assert _select_pending_for_retry([dated], config, {}, _NOW, 0) == []


# ---------- _retry_pending_details ----------

class _FakePage:
    def title(self) -> str:
        return "ok"


def _retry_config() -> dict[str, Any]:
    return _config(_guest=True, pending_detail_retry_max=25,
                   delays={"guest_between_details": [0.01, 0.02]})


def test_retry_pending_recovers_and_counts_failures(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_offer("recover"))
    store.add(_offer("still_empty"))
    store.add(_offer("maxed"))
    for _ in range(_MAX_PENDING_DETAIL_ATTEMPTS):
        store.pending_detail_increment("maxed")

    calls: list[str] = []

    def fake_parse(page, offer, config):
        calls.append(offer.job_id)
        if offer.job_id == "recover":
            offer.description_full = "Descripcion recuperada " + "x" * 100
        return offer

    monkeypatch.setattr(main, "parse_detail", fake_parse)
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)
    progress = RunProgressCounter("linkedin")

    recovered = _retry_pending_details(
        _FakePage(), store, _retry_config(), progress)

    assert recovered == 1
    assert calls == ["recover", "still_empty"]  # "maxed" agoto sus intentos
    assert store.pending_detail_get("recover") is None
    assert store.pending_detail_get("still_empty")["attempts"] == 1
    assert store.pending_detail_get("maxed")["attempts"] == _MAX_PENDING_DETAIL_ATTEMPTS
    assert progress.parsed == 2  # latido PROGRESS en ambos intentos
    state = json.loads(
        (Path(store.checkpoint_dir) / "state.json").read_text(encoding="utf-8"))
    assert "recover" not in state["detail_pending"]
    assert state["detail_pending"]["still_empty"]["attempts"] == 1
    assert store.warning_count >= 1


def test_retry_pending_increments_on_exception(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_offer("boom"))

    def fake_parse(page, offer, config):
        raise RuntimeError("red caida")

    monkeypatch.setattr(main, "parse_detail", fake_parse)
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)
    progress = RunProgressCounter("linkedin")

    recovered = _retry_pending_details(
        _FakePage(), store, _retry_config(), progress)

    assert recovered == 0
    assert store.pending_detail_get("boom")["attempts"] == 1
    assert progress.parsed == 0
    assert store.warning_count >= 1


def test_retry_pending_aborts_when_page_is_dead(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_offer("pending"))
    called: list[str] = []

    def fake_parse(page, offer, config):
        called.append(offer.job_id)
        return offer

    class _DeadPage:
        def title(self) -> str:
            raise RuntimeError("Target page closed")

    monkeypatch.setattr(main, "parse_detail", fake_parse)
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)

    recovered = _retry_pending_details(
        _DeadPage(), store, _retry_config(), RunProgressCounter("linkedin"))

    assert recovered == 0
    assert called == []
    assert store.pending_detail_get("pending") is None


def test_retry_pending_aborts_phase_on_blocked_without_increment(tmp_path,
                                                                monkeypatch):
    """Un challenge corta la fase, no propaga y no penaliza a la oferta."""
    store = _store(tmp_path)
    store.add(_offer("blocked"))
    store.add(_offer("later"))
    store.pending_detail_add("blocked")
    calls: list[str] = []

    def fake_parse(page, offer, config):
        calls.append(offer.job_id)
        raise BlockedException("challenge en detalle")

    monkeypatch.setattr(main, "parse_detail", fake_parse)
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)

    recovered = _retry_pending_details(
        _FakePage(), store, _retry_config(), RunProgressCounter("linkedin"))

    assert recovered == 0
    assert calls == ["blocked"]  # la fase se detiene en la primera oferta
    entry = store.pending_detail_get("blocked")
    assert entry is not None
    assert entry["attempts"] == 0  # el challenge no es culpa del dato
    later = store.pending_detail_get("later")
    assert later is None or later["attempts"] == 0  # ni se llego a intentar
    # El checkpoint de aborto deja el estado persistido.
    state = json.loads(
        (Path(store.checkpoint_dir) / "state.json").read_text(encoding="utf-8"))
    assert state["detail_pending"]["blocked"]["attempts"] == 0


def test_retry_pending_cleans_entries_already_recovered_without_request(
        tmp_path, monkeypatch):
    """Un pendiente ya recuperado se limpia al inicio, sin gastar peticion."""
    store = _store(tmp_path)
    store.add(_offer("recovered", description="Ya recuperada por el SERP."))
    store.add(_offer("still_empty"))
    store.pending_detail_add("recovered")
    store.pending_detail_increment("recovered")
    calls: list[str] = []

    def fake_parse(page, offer, config):
        calls.append(offer.job_id)
        return offer

    monkeypatch.setattr(main, "parse_detail", fake_parse)
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)

    recovered = _retry_pending_details(
        _FakePage(), store, _retry_config(), RunProgressCounter("linkedin"))

    assert "recovered" not in store.detail_pending
    assert "still_empty" in store.detail_pending
    assert calls == ["still_empty"]  # el job ya recuperado no se pide
    assert recovered == 0  # el fake no rellena la descripcion pendiente


def test_retry_pending_without_candidates_is_a_noop(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.add(_offer("done", description="Ya tiene descripcion."))
    called: list[str] = []
    monkeypatch.setattr(main, "parse_detail",
                        lambda page, offer, config: called.append(offer.job_id))
    monkeypatch.setattr(main, "delay", lambda *a, **k: None)

    recovered = _retry_pending_details(
        _FakePage(), store, _retry_config(), RunProgressCounter("linkedin"))

    assert recovered == 0
    assert called == []


def test_pending_helpers_use_iso_first_seen(tmp_path):
    """El primer fallo queda registrado con timestamp ISO utilizable."""
    store = _store(tmp_path)
    before = datetime.now(timezone.utc)
    entry = store.pending_detail_add("100")
    parsed = main._parse_iso_utc(entry["first_seen"])
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed <= datetime.now(timezone.utc)
    assert parsed >= before - timedelta(seconds=5)
