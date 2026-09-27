"""Tests del contador/línea de progreso de LinkedIn (T-16; RF-15).

Sin red ni navegador: se ejercita el contador acumulativo del run.
"""
from __future__ import annotations

from src import main
from src.progress import RunProgressCounter, format_progress_line


def test_T16_parsed_advances_even_when_all_are_known():
    counter = RunProgressCounter("linkedin")

    # Combo 1: SERP cards, all new.
    main._emit_progress(counter, 50, 50)
    # Combo 2: same cards already in the accumulated snapshot -> new == 0,
    # but parsed must still advance (work is being done).
    line = main._emit_progress(counter, 50, 0)

    assert counter.parsed == 100
    assert counter.new == 50
    assert line.startswith("PROGRESS source=linkedin parsed=100 new=50")


def test_T16_detail_visit_counts_as_progress():
    counter = RunProgressCounter("linkedin")

    # A visited detail advances parsed without adding a new offer.
    main._emit_progress(counter, 1, 0)

    assert counter.parsed == 1
    assert counter.new == 0


def test_T16_line_format_is_stable():
    assert format_progress_line("linkedin", 7, 0) == \
        "PROGRESS source=linkedin parsed=7 new=0"
    assert RunProgressCounter("linkedin").line().startswith(
        "PROGRESS source=linkedin parsed=0 new=0"
    )
