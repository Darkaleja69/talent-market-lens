"""Comportamiento "humano": delays aleatorios, scroll incremental lento y jitter
de movimientos de raton. Pensado para no parecer un bot sin eludir controles
de acceso (no resuelve CAPTCHAs, no rotacion de proxies).

TODAS las esperas son random.uniform en rangos configurables, nunca fijas.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

log = logging.getLogger(__name__)


def delay(rng: tuple[float, float], label: str = "") -> None:
    """Espera un tiempo aleatorio uniforme en [rng[0], rng[1]] segundos."""
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
) -> int:
    """Scroll incremental suave dentro de `selector` (o ventana si None).

    Baja `step_px` px por paso, espera `pause_rng` entre pasos, hasta
    `max_steps`. Devuelve el numero de pasos dados. Nunca hace scroll
    instantaneo al final.

    Args:
        page: pagina Playwright.
        step_px_rng: rango de pixels por paso.
        max_steps: limite de pasos (safety para no scrollear infinito).
        pause_rng: rango de pausa entre pasos.
        selector: si se da, scroll dentro de ese elemento (p.ej. panel de
            descripcion de una oferta). Si None, scroll de la ventana.
    """
    target = "document.querySelector(arguments[0])" if selector else "window"
    n = 0
    for _ in range(max_steps):
        step = random.randint(*step_px_rng)
        if selector:
            page.evaluate(
                "([sel, px]) => { const el = document.querySelector(sel);"
                " if (el) el.scrollBy(0, px); }",
                [selector, step],
            )
        else:
            page.evaluate("([px]) => window.scrollBy(0, px)", [step])
        n += 1
        delay(pause_rng, label=f"scroll step {n}")
        # Detectar fin de pagina: si no avanzo el scrollY, salir
        at_bottom = page.evaluate(
            "(tgt) => {"
            "  if (tgt === 'window') return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 5);"
            "  return false;"
            "}",
            target,
        )
        if at_bottom and not selector:
            log.debug("scroll_slow: reached bottom after %d steps", n)
            break
    return n


def mouse_jitter(page, n: int = 1) -> None:
    """Mueve el raton a coordenadas aleatorias dentro del viewport (jitter)."""
    for _ in range(n):
        try:
            x = random.randint(100, 1400)
            y = random.randint(100, 700)
            page.mouse.move(x, y, steps=random.randint(5, 15))
            time.sleep(random.uniform(0.2, 0.6))
        except Exception as e:  # noqa: BLE001 - jitter es best-effort
            log.debug("mouse_jitter omitido: %s", e)


def human_click(page, selector: str, pause_rng: tuple[float, float] = (0.8, 2.0)) -> None:
    """Click con pequeno jitter previo y pausa posterior (best-effort)."""
    mouse_jitter(page, n=1)
    try:
        page.click(selector, timeout=10000)
    except Exception as e:  # noqa: BLE001
        log.debug("human_click fallo en %s: %s", selector, e)
        raise
    delay(pause_rng, label="post-click")
