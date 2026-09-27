"""Per-source retry policy after one scraper attempt (T-21..T-23; RF-12, RF-15).

The PowerShell wrappers must not relaunch a run that the watchdog already
stopped, nor a run blocked by CAPTCHA/anti-bot detection. The decision lives
here so it is testable in Python: the wrappers only read ``decision=stop`` /
``decision=retry`` from the thin CLI and log the reason. No retry rule is
duplicated in PowerShell.

Transversal rules
-----------------
- ``watchdog_blocked=True`` is **never** retried (RF-15): the process tree was
  already halted by the supervisor and relaunching would defeat the watchdog.
- A detected block (``blocked=True``, e.g. InfoJobs ``RESULT ... blocked=true``)
  is **never** retried: it is an anti-bot challenge, not a transient error.

For genuine technical errors the previous per-source behaviour is preserved:

- **Indeed**: retry any technical failure except exit 3 (Cloudflare challenge /
  broken CDP), which is not retried; a ``Scrape completado`` marker means the
  run finished (``completed=True``).
- **LinkedIn**: do not retry the permanent patterns (Apify billing limit /
  login challenge); retry other technical failures.
- **InfoJobs**: retry a normal technical failure; do not retry a block.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

DECISION_RETRY = "retry"
DECISION_STOP = "stop"

REASON_WATCHDOG = "watchdog_no_progress"
REASON_BLOCKED = "blocked_antibot"
REASON_PERMANENT = "permanent_error"
REASON_SUCCESS = "success"
REASON_CHALLENGE = "challenge_antibot"
REASON_TECHNICAL = "technical_error"

# Exit codes of the thin CLI: 0 = retry, 1 = stop.
CLI_RETRY = 0
CLI_STOP = 1


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of evaluating one attempt against the retry policy."""

    source: str
    retry: bool
    reason: str

    @property
    def decision(self) -> str:
        """``retry`` or ``stop``, as printed for the wrappers."""
        return DECISION_RETRY if self.retry else DECISION_STOP


def evaluate_retry(
    source: str,
    *,
    watchdog_blocked: bool = False,
    exit_code: int | None = None,
    permanent: bool = False,
    blocked: bool | None = None,
    completed: bool = False,
) -> RetryDecision:
    """Return the full retry decision (whether to relaunch and why)."""
    name = (source or "").strip().lower()

    # Transversal stops first, in order of authority.
    if watchdog_blocked:
        return RetryDecision(name, False, REASON_WATCHDOG)
    if completed:
        return RetryDecision(name, False, REASON_SUCCESS)
    if blocked is True:
        return RetryDecision(name, False, REASON_BLOCKED)
    if permanent:
        return RetryDecision(name, False, REASON_PERMANENT)
    if exit_code == 0:
        return RetryDecision(name, False, REASON_SUCCESS)

    # Per-source handling of a genuine technical failure.
    if name == "indeed" and exit_code == 3:
        return RetryDecision(name, False, REASON_CHALLENGE)
    return RetryDecision(name, True, REASON_TECHNICAL)


def should_retry(
    source: str,
    *,
    watchdog_blocked: bool = False,
    exit_code: int | None = None,
    permanent: bool = False,
    blocked: bool | None = None,
    completed: bool = False,
) -> bool:
    """Whether the wrapper must relaunch the scraper after this attempt."""
    return evaluate_retry(
        source,
        watchdog_blocked=watchdog_blocked,
        exit_code=exit_code,
        permanent=permanent,
        blocked=blocked,
        completed=completed,
    ).retry


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verification.retry_policy",
        description=(
            "Decide una vez si un wrapper debe relanzar el scraper o detener el "
            "run, y devuelve el motivo en una linea legible por PowerShell."
        ),
    )
    parser.add_argument("--source", required=True, help="source id (indeed, ...)")
    parser.add_argument("--exit-code", type=int, default=None, dest="exit_code")
    parser.add_argument(
        "--watchdog-blocked",
        action="store_true",
        dest="watchdog_blocked",
        help="the supervisor stopped this unit for lack of progress",
    )
    parser.add_argument(
        "--permanent",
        action="store_true",
        help="a permanent error pattern was detected (LinkedIn)",
    )
    parser.add_argument(
        "--completed",
        action="store_true",
        help="the run finished (e.g. Indeed 'Scrape completado')",
    )
    blocked = parser.add_mutually_exclusive_group()
    blocked.add_argument(
        "--blocked",
        dest="blocked",
        action="store_true",
        default=None,
        help="an anti-bot/CAPTCHA block was detected (InfoJobs blocked=true)",
    )
    blocked.add_argument(
        "--not-blocked",
        dest="blocked",
        action="store_false",
        help="a valid RESULT without a block was seen",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print one ``RETRYPOLICY ... decision=... reason=...`` line."""
    args = _build_parser().parse_args(argv)
    decision = evaluate_retry(
        args.source,
        watchdog_blocked=args.watchdog_blocked,
        exit_code=args.exit_code,
        permanent=args.permanent,
        blocked=args.blocked,
        completed=args.completed,
    )
    print(
        "RETRYPOLICY source={source} decision={decision} reason={reason}".format(
            source=decision.source,
            decision=decision.decision,
            reason=decision.reason,
        )
    )
    return CLI_RETRY if decision.retry else CLI_STOP


if __name__ == "__main__":
    raise SystemExit(main())
