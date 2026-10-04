"""Pure incremental offer threshold of a repair (T-08; RF-10).

The live test of a repair must obtain more offers than the greatest count
already verified for that source: ``threshold = max(1, best_verified + 1)``
(plan §9). The rule is pure and stateless: it receives the source's best
verified count (``None`` when there is no history) and returns its threshold.
The threshold never decreases, because ``best_verified`` is the maximum of the
verified counts and each source keeps its own best; reading and updating
``repairs/history.json`` is T-09, which calls this function.

Design rules:

- No I/O, no paths and no dependencies: this module is only the rule.
- ``None`` and ``0`` are equivalent: without history the threshold is the
  minimum (1), because a repair is never proven by zero offers.
"""
from __future__ import annotations

# A repair is never proven by zero offers: without history the threshold is 1.
MINIMUM_THRESHOLD = 1


def incremental_threshold(best_verified: int | None) -> int:
    """Return the threshold of the next repair of a source (RF-10).

    ``best_verified`` is the greatest offer count given as good in previous
    repairs of that source, or ``None`` when there is no history. The result is
    ``max(1, best_verified + 1)``: the next live test must beat the previous
    best by at least one offer, and the first repair needs at least one offer.
    The function is pure, deterministic and per-source: the caller keeps the
    best count of each source (``repairs/history.json``, T-09), so the
    threshold never decreases.
    """
    if best_verified is None:
        return MINIMUM_THRESHOLD
    return max(MINIMUM_THRESHOLD, best_verified + 1)
