"""Tests for the per-source retry policy (T-21..T-23; RF-12, RF-15).

Offline. The PowerShell wrappers cannot run under pytest, so this suite fixes
the decision they consume: a ``stop`` decision is what makes the wrapper break
out of its retry loop without relaunching. Those wrapper-side checks are noted
in ``test_*_loop_would_not_relaunch``.
"""
from __future__ import annotations

import pytest

from verification import retry_policy as rp


SOURCES = ("indeed", "linkedin", "infojobs")


# --------------------------------------------------------------------------
# Transversal: watchdog and detected blocks never retry
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_T21_T22_T23_watchdog_never_retries(source):
    decision = rp.evaluate_retry(
        source, watchdog_blocked=True, exit_code=-1
    )

    assert decision.retry is False
    assert decision.decision == rp.DECISION_STOP
    assert decision.reason == rp.REASON_WATCHDOG
    assert rp.should_retry(source, watchdog_blocked=True, exit_code=-1) is False


@pytest.mark.parametrize("source", SOURCES)
def test_T21_T22_T23_watchdog_beats_any_other_signal(source):
    # Even with a retryable technical error, the watchdog stop wins.
    assert rp.should_retry(
        source,
        watchdog_blocked=True,
        exit_code=-1,
        permanent=False,
        blocked=False,
        completed=False,
    ) is False


def test_T23_infojobs_blocked_true_stops():
    decision = rp.evaluate_retry("infojobs", blocked=True, exit_code=-1)

    assert decision.retry is False
    assert decision.reason == rp.REASON_BLOCKED
    assert rp.should_retry("infojobs", blocked=True, exit_code=-1) is False


# --------------------------------------------------------------------------
# T-21 Indeed: preserve the existing technical-error policy
# --------------------------------------------------------------------------


def test_T21_indeed_technical_error_retries():
    assert rp.should_retry("indeed", exit_code=1) is True
    assert rp.should_retry("indeed", exit_code=2) is True


def test_T21_indeed_exit3_challenge_does_not_retry():
    decision = rp.evaluate_retry("indeed", exit_code=3)

    assert decision.retry is False
    assert decision.reason == rp.REASON_CHALLENGE


def test_T21_indeed_completed_counts_as_ok():
    # "Scrape completado" may arrive with a non-zero exit (resource cleanup).
    decision = rp.evaluate_retry("indeed", exit_code=-1, completed=True)

    assert decision.retry is False
    assert decision.reason == rp.REASON_SUCCESS


# --------------------------------------------------------------------------
# T-22 LinkedIn: permanent patterns stop, other failures retry
# --------------------------------------------------------------------------


def test_T22_linkedin_permanent_pattern_does_not_retry():
    decision = rp.evaluate_retry("linkedin", exit_code=-1, permanent=True)

    assert decision.retry is False
    assert decision.reason == rp.REASON_PERMANENT


def test_T22_linkedin_technical_failure_retries():
    assert rp.should_retry("linkedin", exit_code=-1) is True
    assert rp.should_retry("linkedin", exit_code=4) is True


# --------------------------------------------------------------------------
# T-23 InfoJobs: technical failure retries, block/valid result stops
# --------------------------------------------------------------------------


def test_T23_infojobs_technical_failure_retries():
    # No RESULT line: a normal technical error.
    assert rp.should_retry("infojobs", exit_code=-1) is True


def test_T23_infojobs_valid_result_without_block_stops():
    decision = rp.evaluate_retry("infojobs", blocked=False, exit_code=-1)

    assert decision.retry is False
    assert decision.reason == rp.REASON_VALID_RESULT
    assert rp.should_retry("infojobs", exit_code=0, blocked=False) is False
    # A valid run with zero offers is still a finished run.
    assert rp.should_retry("infojobs", exit_code=1, blocked=False) is False
    assert rp.should_retry("infojobs", exit_code=1, completed=True) is False


# --------------------------------------------------------------------------
# Shared: a zero exit means the run finished for every source
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_exit_zero_stops_every_source(source):
    assert rp.should_retry(source, exit_code=0) is False


def test_unknown_source_retries_technical_failure():
    assert rp.should_retry("brand_new", exit_code=-1) is True


# --------------------------------------------------------------------------
# Thin CLI consumed by the wrappers
# --------------------------------------------------------------------------


def test_cli_stop_line_and_exit_code(capsys):
    code = rp.main(["--source", "linkedin", "--watchdog-blocked", "--exit-code", "-1"])
    out = capsys.readouterr().out

    assert code == rp.CLI_STOP
    assert "decision=stop" in out
    assert f"reason={rp.REASON_WATCHDOG}" in out


def test_cli_retry_line_and_exit_code(capsys):
    code = rp.main(["--source", "indeed", "--exit-code", "1"])
    out = capsys.readouterr().out

    assert code == rp.CLI_RETRY
    assert "decision=retry" in out
    assert f"reason={rp.REASON_TECHNICAL}" in out


def test_cli_infojobs_blocked_stops(capsys):
    code = rp.main(["--source", "infojobs", "--blocked", "--exit-code", "-1"])
    out = capsys.readouterr().out

    assert code == rp.CLI_STOP
    assert "decision=stop" in out
    assert f"reason={rp.REASON_BLOCKED}" in out


def test_cli_infojobs_valid_result_stops(capsys):
    # The wrapper passes --not-blocked when RESULT ... blocked=false.
    code = rp.main(["--source", "infojobs", "--not-blocked", "--exit-code", "-1"])
    out = capsys.readouterr().out

    assert code == rp.CLI_STOP
    assert "decision=stop" in out
    assert f"reason={rp.REASON_VALID_RESULT}" in out


# --------------------------------------------------------------------------
# Wrapper-loop simulation: a stop decision means no relaunch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_wrapper_loop_would_not_relaunch_on_watchdog(source):
    # The wrapper breaks its loop when the policy line matches decision=stop.
    decision = rp.evaluate_retry(source, watchdog_blocked=True, exit_code=-1)

    assert decision.decision == rp.DECISION_STOP  # wrapper: break, no relaunch
    assert decision.reason == rp.REASON_WATCHDOG


def test_wrapper_loop_would_relaunch_on_technical_failure():
    decision = rp.evaluate_retry("indeed", exit_code=1)

    assert decision.decision == rp.DECISION_RETRY
    assert decision.reason == rp.REASON_TECHNICAL
