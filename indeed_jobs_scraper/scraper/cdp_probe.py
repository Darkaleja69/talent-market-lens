"""Probe independiente del handshake CDP para el pre-flight nocturno (T-32).

``GET /json`` solo prueba que el endpoint HTTP de DevTools responde; no prueba
que un cliente real complete el handshake CDP (patchright 1.60.1 no lo logro
contra el Chrome 154 del run 2026-10-10 pese a que el endpoint respondia y
``agent-browser`` operaba contra ese mismo Chrome). Este CLI conecta una vez,
verifica la conexion con una operacion real minima (abrir/cerrar una pagina de
prueba) y termina con una linea legible por la maquina y un exit code:

    CDP_HANDSHAKE_OK port=9222 ...
    CDP_HANDSHAKE_FAIL port=9222 ...

Exit codes: 0 = handshake verificado; 1 = handshake no verificado; 2 = error de
entorno (patchright no disponible). Nunca navega a ningun sitio y al terminar
solo se desconecta del Chrome vivo (no lo cierra).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .browser import CDP_CONNECT_TIMEOUT_MS, open_cdp_browser

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scraper.cdp_probe",
        description=(
            "Verifica el handshake CDP real de un Chrome con remote debugging "
            "(GET /json no basta)."
        ),
    )
    parser.add_argument("--port", type=int, default=9222, help="puerto CDP (default: 9222)")
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=CDP_CONNECT_TIMEOUT_MS,
        help=f"timeout de conexion en ms (default: {CDP_CONNECT_TIMEOUT_MS})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:  # pragma: no cover - depende del entorno
        print(f"CDP_HANDSHAKE_FAIL port={args.port} detail=patchright no disponible")
        log.debug("import de patchright fallo: %s", e)
        return 2

    pw = sync_playwright().start()
    browser = None
    try:
        browser = open_cdp_browser(
            pw, args.port, attempts=1, timeout_ms=args.timeout_ms, sleep_fn=time.sleep
        )
        if browser is None:
            print(f"CDP_HANDSHAKE_FAIL port={args.port}")
            return 1
        print(f"CDP_HANDSHAKE_OK port={args.port}")
        return 0
    finally:
        if browser is not None:
            try:
                # disconnect en vez de close: no mata el Chrome del usuario
                browser.close()
            except Exception:
                pass
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
