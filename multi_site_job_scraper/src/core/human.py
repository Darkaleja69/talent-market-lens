"""Comportamiento "humano" para scrapers Playwright: delays aleatorios,
scroll incremental lento y jitter de raton.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

log = logging.getLogger(__name__)


def delay(rng: tuple[float, float], label: str = "") -> None:
    t = random.uniform(*rng)
    if label:
        log.debug("delay %s: %.2fs", label, t)
    time.sleep(t)


def scroll_slow(
    page,
    step_px_rng: tuple[int, int] = (200, 400),
    max_steps: int = 25,
    pause_rng: tuple[float, float] = (1.5, 3.5),
    selector: Optional[str] = None,
    early_card_count: int = 0,
) -> int:
    """Scroll incremental. early_card_count: si > 0, parar en cuanto se
    detecten esa cantidad de tarjetas (no hace falta llegar al fondo)."""
    n = 0
    for _ in range(max_steps):
        step = random.randint(*step_px_rng)
        if selector:
            page.evaluate(
                "([sel, px]) => { const el = document.querySelector(sel);"
                " if (el) el.scrollBy(0, px); }", [selector, step])
        else:
            page.evaluate("([px]) => window.scrollBy(0, px)", [step])
        n += 1

        if early_card_count > 0 and n >= 2:
            try:
                current_cards = page.evaluate(
                    """() => document.querySelectorAll(
                        '[data-testid="job-item"], [data-testid="job-card-content"], [data-testid="job-card"]'
                    ).length"""
                )
                if current_cards and current_cards >= early_card_count:
                    break
            except Exception:
                pass

        delay(pause_rng, label=f"scroll step {n}")
        at_bottom = page.evaluate(
            "() => (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 5)")
        if at_bottom and not selector:
            break
    return n


def mouse_jitter(page, n: int = 1) -> None:
    for _ in range(n):
        try:
            x = random.randint(100, 1400)
            y = random.randint(100, 700)
            page.mouse.move(x, y, steps=random.randint(5, 15))
            time.sleep(random.uniform(0.2, 0.6))
        except Exception:
            pass


def human_click(page, selector: str,
                pause_rng: tuple[float, float] = (0.8, 2.0)) -> None:
    mouse_jitter(page, n=1)
    page.click(selector, timeout=10000)
    delay(pause_rng, label="post-click")
