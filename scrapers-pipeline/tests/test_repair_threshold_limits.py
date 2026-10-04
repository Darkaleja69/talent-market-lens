"""End-to-end limit tests of the incremental threshold (T-10; RF-10).

Scenario tests for ``repair.threshold`` over ``tmp_path``: the first repair
starts at one, the bar rises by one offer, the threshold never decreases
across disk round-trips, sources keep independent thresholds, and a
manipulated history is either respected (absurd but valid) or discarded with a
warning (invalid values). These are integration scenarios of the T-08/T-09
pieces; the corrupt-file matrix stays in ``test_repair_threshold.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from repair import threshold


def _history_path(tmp_path: Path) -> Path:
    return tmp_path / "repairs" / "history.json"


def _record(path: Path, source: str, offers: int) -> dict:
    """Load, record a verified count and persist it, as T-14 will do."""
    loaded = threshold.load_history(path)
    updated = threshold.record_verified(loaded.history, source, offers)
    threshold.save_history(updated, path)
    return updated


def _write_history(path: Path, source: str, value: object) -> None:
    """Write a hand-made history with one entry, valid or not."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {source: {"best_verified_offers": value}},
            }
        ),
        encoding="utf-8",
    )


# --- First repair and raising the bar ----------------------------------------


def test_first_repair_starts_at_one_and_persists_the_verified_offer(tmp_path):
    path = _history_path(tmp_path)

    loaded = threshold.load_history(path)
    assert loaded.warnings == ()
    assert threshold.threshold_for(loaded.history, "infojobs") == 1

    history = threshold.record_verified(loaded.history, "infojobs", 1)
    threshold.save_history(history, path)

    reloaded = threshold.load_history(path)
    assert reloaded.warnings == ()
    assert threshold.best_verified(reloaded.history, "infojobs") == 1
    assert threshold.threshold_for(reloaded.history, "infojobs") == 2


def test_raising_the_bar_requires_one_more_offer(tmp_path):
    path = _history_path(tmp_path)
    _record(path, "indeed", 2)

    reloaded = threshold.load_history(path)

    assert threshold.best_verified(reloaded.history, "indeed") == 2
    assert threshold.threshold_for(reloaded.history, "indeed") == 3


def test_threshold_never_decreases_across_round_trips(tmp_path):
    path = _history_path(tmp_path)
    _record(path, "linkedin", 5)
    _record(path, "linkedin", 3)
    _record(path, "linkedin", 0)

    reloaded = threshold.load_history(path)

    assert threshold.best_verified(reloaded.history, "linkedin") == 5
    assert threshold.threshold_for(reloaded.history, "linkedin") == 6


# --- Source isolation --------------------------------------------------------


def test_sources_keep_independent_thresholds_in_memory_and_on_disk(tmp_path):
    path = _history_path(tmp_path)
    _record(path, "indeed", 2)
    _record(path, "nvb", 10)

    reloaded = threshold.load_history(path)
    assert threshold.threshold_for(reloaded.history, "indeed") == 3
    assert threshold.threshold_for(reloaded.history, "nvb") == 11
    assert threshold.threshold_for(reloaded.history, "jobs_ch") == 1

    # Updating one source does not touch the other, in memory...
    updated = threshold.record_verified(reloaded.history, "indeed", 4)
    assert threshold.best_verified(updated, "indeed") == 4
    assert threshold.best_verified(updated, "nvb") == 10

    # ...nor on disk.
    threshold.save_history(updated, path)
    after = threshold.load_history(path)
    assert threshold.best_verified(after.history, "indeed") == 4
    assert threshold.best_verified(after.history, "nvb") == 10
    assert threshold.threshold_for(after.history, "indeed") == 5
    assert threshold.threshold_for(after.history, "nvb") == 11


# --- Manipulated history -----------------------------------------------------


def test_absurd_but_valid_history_is_respected(tmp_path):
    path = _history_path(tmp_path)
    _write_history(path, "indeed", 999999)

    loaded = threshold.load_history(path)

    assert loaded.warnings == ()
    assert threshold.best_verified(loaded.history, "indeed") == 999999
    assert threshold.threshold_for(loaded.history, "indeed") == 1000000


@pytest.mark.parametrize("invalid", [-1, 1.5, True])
def test_invalid_history_value_is_discarded_with_warning(tmp_path, invalid):
    path = _history_path(tmp_path)
    _write_history(path, "indeed", invalid)

    loaded = threshold.load_history(path)

    assert loaded.history == threshold.empty_history()
    assert len(loaded.warnings) == 1
    assert "vacío" in loaded.warnings[0]
    assert threshold.threshold_for(loaded.history, "indeed") == 1


def test_record_verified_rejects_invalid_counts_without_corrupting(tmp_path):
    path = _history_path(tmp_path)
    _record(path, "indeed", 2)
    before = path.read_text(encoding="utf-8")

    for invalid in (-1, 1.5, True, "3", None):
        with pytest.raises(ValueError):
            threshold.record_verified(
                threshold.load_history(path).history, "indeed", invalid
            )

    # The history on disk is untouched and still usable.
    assert path.read_text(encoding="utf-8") == before
    reloaded = threshold.load_history(path)
    assert threshold.best_verified(reloaded.history, "indeed") == 2
    assert threshold.threshold_for(reloaded.history, "indeed") == 3
