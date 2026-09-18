"""Tests del modo de trabajo derivado del detalle de jobs.ch.

jobs.ch no expone work_mode estructurado; se deriva del texto (Homeoffice,
Remote, Hybrid...). Estos tests fijan esa derivacion.
"""
from src.jobs_ch.detail import _find_work_mode


def test_home_office_is_remote():
    text = ("modern working environment with flexible working models "
            "and home office options")
    assert _find_work_mode(text) == "Remote"


def test_homeoffice_german():
    assert _find_work_mode("Flexible Arbeitsmodelle und Homeoffice-Möglichkeiten") == "Remote"


def test_explicit_hybrid_wins():
    assert _find_work_mode("Hybrid working model with 2 days remote") == "Hybrid"


def test_onsite():
    assert _find_work_mode("This is a fully on-site role in Zurich") == "On-site"


def test_no_signal_is_empty():
    assert _find_work_mode("We are a great team looking for talent.") == ""
