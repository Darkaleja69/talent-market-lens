"""Tests unitarios de la cache persistente de descripciones."""
import json

from scraper.desc_cache import (
    apply_cache_to_offers,
    cache_size,
    load_cache,
    save_cache,
    update_cache_from_offers,
)
from scraper.models import JobOffer


def _offer(jk: str, desc_html: str = "") -> JobOffer:
    o = JobOffer(job_key=jk)
    if desc_html:
        o.description_html = desc_html
        o.description_text = "texto limpio" * 10
    return o


class TestLoadSave:
    def test_load_missing_returns_empty(self, tmp_path):
        assert load_cache(tmp_path / "nope.json") == {}

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "cache.json"
        jobs = {"abc": {"description_html": "<p>hola</p>"}}
        save_cache(path, jobs)
        assert load_cache(path) == jobs

    def test_corrupt_returns_empty(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert load_cache(path) == {}

    def test_wrong_shape_returns_empty(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text(json.dumps({"jobs": "not a dict"}), encoding="utf-8")
        assert load_cache(path) == {}


class TestApplyCache:
    def test_fills_offer_without_clicks(self):
        desc = "<p>" + ("descripcion larga " * 10) + "</p>"
        cache = {"jk1": {"description_html": desc, "benefits": "Seguro; Gym"}}
        offer = _offer("jk1")
        filled = apply_cache_to_offers([offer], cache)
        assert filled == 1
        assert offer.description_html == desc
        assert offer.description_text
        assert offer.benefits == "Seguro; Gym"

    def test_skips_offer_with_existing_description(self):
        cache = {"jk1": {"description_html": "<p>" + ("x" * 100) + "</p>"}}
        offer = _offer("jk1", desc_html="<p>" + ("y" * 100) + "</p>")
        assert apply_cache_to_offers([offer], cache) == 0
        assert "y" in offer.description_html

    def test_ignores_unknown_key(self):
        cache = {"other": {"description_html": "<p>" + ("x" * 100) + "</p>"}}
        assert apply_cache_to_offers([_offer("jk1")], cache) == 0

    def test_empty_cache(self):
        assert apply_cache_to_offers([_offer("jk1")], {}) == 0


class TestUpdateCache:
    def test_stores_only_with_description(self):
        cache = {}
        offers = [
            _offer("jk1", desc_html="<p>" + ("a" * 100) + "</p>"),
            _offer("jk2"),
        ]
        changed = update_cache_from_offers(cache, offers)
        assert changed == 1
        assert "jk1" in cache
        assert "jk2" not in cache

    def test_second_update_is_noop(self):
        cache = {}
        offers = [_offer("jk1", desc_html="<p>" + ("a" * 100) + "</p>")]
        update_cache_from_offers(cache, offers)
        assert update_cache_from_offers(cache, offers) == 0

    def test_cache_size(self):
        cache = {"a": {}, "b": {}}
        assert cache_size(cache) == 2
