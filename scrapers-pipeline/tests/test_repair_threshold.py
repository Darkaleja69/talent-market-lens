"""Tests for the incremental threshold and the verified-count history (RF-10).

Offline tests for ``repair.threshold``: the pure rule of T-08 (no history and
zero yield the minimum, a previous best yields one above it, strictly
increasing and never decreasing per source) and the history I/O of T-09
(missing/corrupt file tolerance, never-decreasing update, independent sources,
atomic UTF-8 writes and the deterministic round-trip).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from repair import threshold


def test_no_history_yields_the_minimum_threshold():
    assert threshold.incremental_threshold(None) == 1


def test_zero_verified_count_yields_the_minimum_threshold():
    assert threshold.incremental_threshold(0) == 1


def test_none_and_zero_are_equivalent():
    assert threshold.incremental_threshold(
        None
    ) == threshold.incremental_threshold(0)


@pytest.mark.parametrize(
    ("best_verified", "expected"),
    [(1, 2), (2, 3), (3, 4), (50, 51), (999, 1000)],
)
def test_threshold_is_one_above_the_verified_best(best_verified, expected):
    assert threshold.incremental_threshold(best_verified) == expected


def test_threshold_is_strictly_increasing_with_the_verified_best():
    bests = [0, 1, 2, 5, 50]

    thresholds = [threshold.incremental_threshold(best) for best in bests]

    assert thresholds == [1, 2, 3, 6, 51]
    assert all(
        current > previous
        for previous, current in zip(thresholds, thresholds[1:])
    )


def test_defensive_values_stay_at_the_minimum():
    assert threshold.incremental_threshold(-5) == 1
    assert threshold.incremental_threshold(-1) == 1


def test_minimum_threshold_constant():
    assert threshold.MINIMUM_THRESHOLD == 1
    assert threshold.incremental_threshold(None) == threshold.MINIMUM_THRESHOLD


# --- History I/O (T-09; RF-10) -----------------------------------------------


def _history_path(tmp_path: Path) -> Path:
    return tmp_path / "repairs" / "history.json"


def test_default_history_path_is_anchored_to_the_repository():
    expected = (
        Path(threshold.__file__).resolve().parents[2]
        / "repairs"
        / "history.json"
    )

    assert threshold.DEFAULT_HISTORY_PATH == expected
    assert threshold.DEFAULT_HISTORY_PATH.name == "history.json"


def test_empty_history_has_schema_version_and_sources():
    assert threshold.empty_history() == {"schema_version": 1, "sources": {}}
    assert threshold.empty_history() is not threshold.empty_history()


def test_missing_history_loads_empty_without_warning(tmp_path):
    path = _history_path(tmp_path)

    loaded = threshold.load_history(path)

    assert loaded.path == path
    assert loaded.history == threshold.empty_history()
    assert loaded.warnings == ()
    assert not path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "{no es json",
        "[1, 2, 3]",
        '{"schema_version": 1, "sources": []}',
        '{"schema_version": 1, "sources": {"indeed": 2}}',
        '{"schema_version": 1, "sources": {"indeed": {"best_verified_offers": "2"}}}',
        '{"schema_version": 1, "sources": {"indeed": {"best_verified_offers": -1}}}',
        '{"schema_version": 2, "sources": {}}',
        '{"sources": {}}',
    ],
)
def test_corrupt_history_loads_empty_with_spanish_warning(tmp_path, payload):
    path = tmp_path / "history.json"
    path.write_text(payload, encoding="utf-8")

    loaded = threshold.load_history(path)

    assert loaded.history == threshold.empty_history()
    assert len(loaded.warnings) == 1
    warning = loaded.warnings[0]
    assert "historial" in warning
    assert "vacío" in warning
    # Loading never touches the file.
    assert path.read_text(encoding="utf-8") == payload


def test_unreadable_history_loads_empty_with_spanish_warning(tmp_path):
    path = tmp_path / "history.json"
    path.mkdir()

    loaded = threshold.load_history(path)

    assert loaded.history == threshold.empty_history()
    assert len(loaded.warnings) == 1
    assert "no se pudo leer" in loaded.warnings[0]


def test_record_verified_never_decreases():
    history = threshold.empty_history()

    history = threshold.record_verified(history, "indeed", 2)
    assert threshold.best_verified(history, "indeed") == 2

    history = threshold.record_verified(history, "indeed", 1)
    assert threshold.best_verified(history, "indeed") == 2

    history = threshold.record_verified(history, "indeed", 2)
    assert threshold.best_verified(history, "indeed") == 2


def test_record_verified_keeps_zero_at_zero():
    history = threshold.record_verified(threshold.empty_history(), "nvb", 0)

    assert threshold.best_verified(history, "nvb") == 0


def test_sources_are_independent_and_the_input_is_not_mutated():
    first = threshold.record_verified(threshold.empty_history(), "indeed", 2)

    updated = threshold.record_verified(first, "linkedin", 5)

    assert threshold.best_verified(updated, "indeed") == 2
    assert threshold.best_verified(updated, "linkedin") == 5
    assert threshold.best_verified(first, "linkedin") is None
    assert first == {
        "schema_version": 1,
        "sources": {"indeed": {"best_verified_offers": 2}},
    }


def test_record_verified_keeps_unknown_entry_keys():
    history = {
        "schema_version": 1,
        "sources": {
            "indeed": {"best_verified_offers": 2, "last_result": "proven"}
        },
    }

    updated = threshold.record_verified(history, "indeed", 3)

    assert updated["sources"]["indeed"] == {
        "best_verified_offers": 3,
        "last_result": "proven",
    }


@pytest.mark.parametrize("offers", [-1, 1.5, "3", True, None])
def test_record_verified_rejects_invalid_counts(offers):
    with pytest.raises(ValueError):
        threshold.record_verified(threshold.empty_history(), "indeed", offers)


def test_threshold_for_uses_the_history():
    history = threshold.empty_history()

    assert threshold.threshold_for(history, "indeed") == 1

    history = threshold.record_verified(history, "indeed", 2)
    assert threshold.threshold_for(history, "indeed") == 3
    assert threshold.threshold_for(history, "linkedin") == 1


def test_best_verified_is_defensive_with_a_malformed_history():
    assert threshold.best_verified({}, "indeed") is None
    assert threshold.best_verified({"sources": []}, "indeed") is None
    assert (
        threshold.best_verified({"sources": {"indeed": 2}}, "indeed") is None
    )
    assert (
        threshold.best_verified(
            {"sources": {"indeed": {"best_verified_offers": True}}}, "indeed"
        )
        is None
    )


def test_save_and_load_round_trip_is_deterministic(tmp_path):
    path = _history_path(tmp_path)
    history = threshold.record_verified(threshold.empty_history(), "indeed", 2)

    written = threshold.save_history(history, path)

    assert written == path
    loaded = threshold.load_history(path)
    assert loaded.history == history
    assert loaded.warnings == ()

    # Saving the loaded history again overwrites with the same content.
    threshold.save_history(loaded.history, path)
    assert threshold.load_history(path).history == history


def test_saved_history_format_is_stable_english_and_utf8(tmp_path):
    path = tmp_path / "history.json"
    history = threshold.record_verified(threshold.empty_history(), "jobs_ch", 4)

    threshold.save_history(history, path)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert '"schema_version": 1' in text
    assert '"sources"' in text
    assert '"jobs_ch"' in text
    assert '"best_verified_offers": 4' in text
    assert "\n  " in text  # two-space indentation
    assert json.loads(text) == history
    # No orphan temporary files are left next to the history.
    assert [entry.name for entry in tmp_path.iterdir()] == ["history.json"]


def test_failed_save_removes_its_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "history.json"

    def _broken_replace(source, destination):
        raise OSError("disco lleno")

    monkeypatch.setattr(threshold.os, "replace", _broken_replace)

    with pytest.raises(OSError):
        threshold.save_history(threshold.empty_history(), path)

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_load_history_preserves_enriched_entries(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    "indeed": {
                        "best_verified_offers": 2,
                        "last_result": "proven",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = threshold.load_history(path)

    assert loaded.warnings == ()
    assert loaded.history["sources"]["indeed"]["last_result"] == "proven"
