"""Tests for the Spanish console presentation of the repair queue (T-06; RF-1).

Offline tests for ``repair.presentation``: header and ordering of the real
fixture queue, the summary of a blocked primary (InfoJobs) and of secondary
field gaps (NVB, LinkedIn), the generic playbook for an unknown source,
unpublished optionals, and the rule that machine identifiers stay in English
while no user path or credential leaks into the text.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

from repair import brief, presentation, targets

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REAL_FIXTURE = _FIXTURES_DIR / "diagnostic_2026-10-03.sanitized.json"


def _queue():
    loaded = targets.load_diagnostic(_REAL_FIXTURE)
    return targets.select_all_targets(loaded)


def _blocks(text: str) -> dict[str, str]:
    """Split the rendered text into numbered target blocks by key."""
    blocks: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^(\d+)\. (\S+)", line)
        if match is not None:
            current = f"{match.group(1)}. {match.group(2)}"
            blocks[current] = line + "\n"
        elif current is not None:
            blocks[current] += line + "\n"
    return blocks


# --- Header and ordering -----------------------------------------------------


def test_header_reports_run_and_counts():
    text = presentation.describe_targets(_queue())

    assert "Objetivos de reparación — run de origen: 2026-10-03" in text
    assert "Total: 19 objetivos (5 primarios, 14 secundarios)" in text


def test_primary_targets_are_listed_first_in_diagnostic_order():
    text = presentation.describe_targets(_queue())

    positions = [
        text.index(f"{position}. {source}")
        for position, source in enumerate(
            ["indeed", "linkedin", "infojobs", "irishjobs", "glassdoor"],
            start=1,
        )
    ]
    assert positions == sorted(positions)
    # The first secondary comes after the fifth primary, and the last
    # secondary is the Jobs.ch work_mode investigation.
    assert text.index("5. glassdoor") < text.index("6. indeed")
    assert "19. jobs_ch" in text


def test_multiple_runs_are_listed_in_the_header():
    base = _queue()[0]
    other = replace(base, run_date=date(2026, 10, 2))

    text = presentation.describe_targets((base, other))

    assert "runs de origen: 2026-10-03, 2026-10-02" in text


def test_empty_queue_reports_no_targets():
    text = presentation.describe_targets(())

    assert "run de origen: desconocido" in text
    assert "Total: 0 objetivos (0 primarios, 0 secundarios)" in text
    assert "No hay objetivos que reparar." in text


# --- Real primary and secondary summaries ------------------------------------


def test_infojobs_block_shows_state_reason_gap_and_playbook():
    block = _blocks(presentation.describe_targets(_queue()))["3. infojobs"]

    assert "3. infojobs — primario (primary) | kind: direct" in block
    assert (
        "Estado: failed | outcome: blocked (bloqueo por CAPTCHA/anti-bot)"
        in block
    )
    assert "Motivo: sin evidencia suficiente para confirmar la fuente" in block
    assert "Brecha de calidad:" in block
    assert "title sin dato → 100 %" in block
    assert "skills: sin meta (el portal no lo publica)" in block
    assert "Playbook: §6.1 InfoJobs — bloqueo por CAPTCHA" in block


def test_nvb_secondary_block_shows_field_gap_and_playbook():
    block = _blocks(presentation.describe_targets(_queue()))["16. nvb"]

    assert "16. nvb — secundario (secondary) | kind: multi_site" in block
    assert "Estado: ok | outcome: ok (ejecución correcta)" in block
    assert (
        "Motivo: optional_field_at_or_below_threshold "
        "(campo opcional en el umbral o por debajo) | campo: work_mode | "
        "actual: 0.0 %"
    ) in block
    assert "work_mode 0.0 % → 100 %" in block
    assert "Playbook: §6.6 NVB — completitud de campos opcionales" in block


def test_required_secondary_block_shows_trigger_field_and_gap():
    block = _blocks(presentation.describe_targets(_queue()))["10. linkedin"]

    assert (
        "required_field_below_target (campo obligatorio por debajo del "
        "objetivo)" in block
    )
    assert "campo: description | actual: 88.4 %" in block
    assert "description 88.4 % → 100 %" in block
    assert "Playbook: §6.3 LinkedIn" in block


def test_unpublished_optional_is_reported_without_meta():
    block = _blocks(presentation.describe_targets(_queue()))["1. indeed"]

    assert "skills: sin meta (el portal no lo publica)" in block
    assert "skills 0.0 %" not in block
    assert "description 0.0 % → 100 %" in block
    assert "salary 35.1 % → 100 %" in block
    assert "work_mode 17.5 % → 100 %" in block


def test_unknown_source_uses_the_generic_playbook():
    unknown = replace(
        next(target for target in _queue() if target.source == "indeed"),
        source="nuevafuente",
    )

    text = presentation.describe_targets((unknown,))

    assert "1. nuevafuente — primario (primary)" in text
    assert "Playbook: §6.7 genérico para fuentes nuevas o fallos futuros" in text
    assert "Total: 1 objetivo (1 primario, 0 secundarios)" in text


# --- Machine identifiers and hygiene -----------------------------------------


def test_machine_identifiers_stay_in_english():
    text = presentation.describe_targets(_queue())

    for token in (
        "indeed",
        "linkedin",
        "infojobs",
        "irishjobs",
        "glassdoor",
        "stepstone_nl",
        "nvb",
        "jobs_ch",
        "primary",
        "secondary",
        "direct",
        "multi_site",
        "failed",
        "ok",
        "blocked",
        "error",
        "description",
        "salary",
        "skills",
        "work_mode",
        "required_field_below_target",
        "optional_field_at_or_below_threshold",
        "§6.1",
        "§6.2",
        "§6.3",
        "§6.4",
        "§6.5",
        "§6.6",
    ):
        assert token in text


def test_text_has_no_user_paths_or_credentials():
    text = presentation.describe_targets(_queue())

    assert "aleja" not in text
    assert "AppData" not in text
    assert "C:\\" not in text
    assert "password" not in text.lower()
    assert "token" not in text.lower()
    assert "secret" not in text.lower()


def test_playbook_sections_match_the_brief():
    for target in _queue():
        section = brief.build_brief(target)["playbook"]["section"]
        text = presentation.describe_targets((target,))
        assert f"Playbook: §{section} " in text
