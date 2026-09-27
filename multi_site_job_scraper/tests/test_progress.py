"""Tests del contador/línea de progreso por portal Multi-site (T-18; RF-15).

Sin red ni navegador: se ejercita el contador acumulativo de cada portal.
"""
from __future__ import annotations

import pytest

from src.core.progress import RunProgressCounter, format_progress_line
from src.devitjobs.config_nl import DEFAULT_CONFIG as DEVITJOBS_CFG
from src.devitjobs.scraper import DevITJobsScraper
from src.glassdoor.config_gd import DEFAULT_CONFIG as GLASSDOOR_CFG
from src.glassdoor.scraper import GlassdoorScraper
from src.irishjobs.config_ie import DEFAULT_CONFIG as IRISHJOBS_CFG
from src.irishjobs.scraper import IrishJobsScraper
from src.jobs_ch.config_ch import DEFAULT_CONFIG as JOBS_CH_CFG
from src.jobs_ch.scraper import JobsChScraper
from src.nvb.config_nl import DEFAULT_CONFIG as NVB_CFG
from src.nvb.scraper import NvbScraper
from src.stepstone_nl.config_nl import DEFAULT_CONFIG as STEPSTONE_CFG

# (scraper class, default config, expected source id)
PORTALS = [
    (IrishJobsScraper, IRISHJOBS_CFG, "irishjobs"),
    (IrishJobsScraper, STEPSTONE_CFG, "stepstone_nl"),
    (DevITJobsScraper, DEVITJOBS_CFG, "devitjobs"),
    (NvbScraper, NVB_CFG, "nvb"),
    (JobsChScraper, JOBS_CH_CFG, "jobs_ch"),
    (GlassdoorScraper, GLASSDOOR_CFG, "glassdoor"),
]


def _cfg(default: dict, tmp_path) -> dict:
    cfg = dict(default)
    cfg["output"] = {
        "csv": str(tmp_path / "jobs.csv"),
        "parquet": str(tmp_path / "jobs.parquet"),
        "checkpoint_dir": str(tmp_path / "cp"),
    }
    return cfg


def test_T18_line_format_is_stable():
    assert format_progress_line("nvb", 4, 1) == "PROGRESS source=nvb parsed=4 new=1"
    assert RunProgressCounter("devitjobs").line().startswith(
        "PROGRESS source=devitjobs parsed=0 new=0"
    )


def test_T18_portal_counters_are_independent():
    irish = RunProgressCounter("irishjobs")
    step = RunProgressCounter("stepstone_nl")

    irish.add(3, 1)
    assert irish.parsed == 3
    assert step.parsed == 0  # the advance of one portal is not attributed to another

    step.add(7, 0)
    assert irish.parsed == 3
    assert step.parsed == 7
    assert "source=irishjobs" in irish.line()
    assert "source=stepstone_nl" in step.line()


def test_T18_six_portals_map_to_distinct_sources():
    sites = {default["site"] for _, default, _ in PORTALS}
    assert sites == {
        "irishjobs", "stepstone_nl", "devitjobs",
        "nvb", "jobs_ch", "glassdoor",
    }


@pytest.mark.parametrize("scraper_cls, default, site", PORTALS)
def test_T18_each_portal_emits_its_own_source(tmp_path, scraper_cls, default, site):
    scraper = scraper_cls(_cfg(default, tmp_path))

    line = scraper._emit_progress(5, 2)

    assert scraper._progress.source == site
    assert scraper._progress.parsed == 5
    assert scraper._progress.new == 2
    assert line.startswith(f"PROGRESS source={site} parsed=5 new=2")
