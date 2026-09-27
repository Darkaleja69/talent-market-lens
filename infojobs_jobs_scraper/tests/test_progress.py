"""Tests del contador/línea de progreso de InfoJobs (T-17; RF-15).

Sin red ni navegador: se ejercita el contador acumulativo del run.
"""
from __future__ import annotations

from scraper import main
from scraper.progress import RunProgressCounter, format_progress_line


def test_T17_counter_updates_and_keeps_run_total():
    counter = RunProgressCounter("infojobs")

    # Page 1: 20 parsed, 5 new. Page 2: 30 parsed, all already seen.
    main._emit_progress(counter, 20, 5)
    line = main._emit_progress(counter, 30, 0)

    # The run total is preserved even when a page adds nothing new.
    assert counter.parsed == 50
    assert counter.new == 5
    assert line.startswith("PROGRESS source=infojobs parsed=50 new=5")


def test_T17_line_format_is_stable():
    assert format_progress_line("infojobs", 3, 1) == \
        "PROGRESS source=infojobs parsed=3 new=1"
    assert RunProgressCounter("infojobs").line().startswith(
        "PROGRESS source=infojobs parsed=0 new=0"
    )
