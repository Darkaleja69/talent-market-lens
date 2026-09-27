"""Tests del contador/línea de progreso de Indeed (T-15; RF-15).

Sin red ni navegador: se ejercita el contador acumulativo del run.
"""
from __future__ import annotations

import re

from scraper.models import JobOffer
from scraper.progress import RunProgressCounter, format_progress_line
from scraper.runner import ScraperRunner

# Formato exigido por el watchdog del pipeline.
LINE_RE = re.compile(
    r"^PROGRESS source=(\S+) parsed=(\d+)(?: new=(\d+))?(?: ts=\S+)?$"
)


def _runner(tmp_path) -> ScraperRunner:
    return ScraperRunner("ES", ["Madrid"], ["data"], 1, tmp_path)


def test_T15_counter_advances_when_parsing_offers(tmp_path):
    runner = _runner(tmp_path)

    line = runner._record_page_progress(
        [JobOffer(job_key="a"), JobOffer(job_key="b")]
    )

    assert runner._progress.parsed == 2
    assert runner._progress.new == 2
    match = LINE_RE.match(line)
    assert match is not None
    assert match.group(1) == "indeed"
    assert int(match.group(2)) == 2


def test_T15_counter_includes_known_duplicates(tmp_path):
    runner = _runner(tmp_path)
    runner._record_page_progress([JobOffer(job_key="a"), JobOffer(job_key="b")])

    # Second page: "a" is a duplicate already known; parsed must still grow.
    runner._record_page_progress([JobOffer(job_key="a"), JobOffer(job_key="c")])

    assert runner._progress.parsed == 4
    assert runner._progress.new == 3


def test_T15_progress_does_not_depend_on_generic_log_activity(tmp_path):
    runner = _runner(tmp_path)

    # No offers parsed -> the counter does not move.
    runner._record_page_progress([])
    assert runner._progress.parsed == 0

    runner._record_page_progress([JobOffer(job_key="x")])
    assert runner._progress.parsed == 1


def test_T15_line_format_is_stable():
    line = format_progress_line(
        "indeed", 10, 3, at="2026-09-26T00:00:00+00:00"
    )
    assert line == "PROGRESS source=indeed parsed=10 new=3 ts=2026-09-26T00:00:00+00:00"
    assert RunProgressCounter("indeed").line() == "PROGRESS source=indeed parsed=0 new=0"
