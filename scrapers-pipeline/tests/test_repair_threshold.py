"""Tests for the pure incremental threshold rule (T-08; RF-10).

Offline tests for ``repair.threshold``: no history (``None``) and zero yield
the minimum, a previous best yields one above it, and the rule is strictly
increasing and never decreasing per source.
"""
from __future__ import annotations

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
