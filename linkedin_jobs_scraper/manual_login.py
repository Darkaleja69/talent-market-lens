"""Login MANUAL asistido para LinkedIn (sin automatizar credenciales).

Por que existe:
  El flujo automatico de src/login.py rellena email/password por script en un
  navegador controlado. Eso es exactamente lo que dispara los challenges de
  LinkedIn ("/checkpoint/challenge/") una y otra vez. Este script hace lo
  contrario: abre la ventana y DEJA QUE TU escribas las credenciales, como en
  un navegador normal. El script solo vigila cuando la sesion queda iniciada,
  la guarda en auth/storage_state.json y deja el perfil persistente listo.

  No introduce credenciales, no pulsa botones de login, no resuelve CAPTCHAs.

Uso:
    python manual_login.py
    python manual_login.py --timeout-minutes 30

Tras capturar la sesion:
    python -m src.main --role "Data Analyst" --city Madrid --append
"""
from __future__ import annotations

import argparse
import time

from src.browser import close_context, launch_context, save_storage_state
from src.login import _is_logged_in
from src.main import load_config

LOGIN_URL = "https://www.linkedin.com/login"
JOBS_URL = "https://www.linkedin.com/jobs/"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Captura manual de la sesion de LinkedIn.")
    ap.add_argument("--timeout-minutes", type=int, default=20,
                    help="Minutos maximos a esperar a que inicies sesion (default 20).")
    args = ap.parse_args()

    config = load_config()

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright  # type: ignore

    print("=" * 72)
    print("LOGIN MANUAL DE LINKEDIN")
    print("  1. En la ventana que se abre, escribe tu email y contrasena TU MISMO.")
    print("  2. Si aparece CAPTCHA/verificacion, resuelvelo en la ventana.")
    print("  3. Completa el 2FA (email/SMS) si te lo pide.")
    print("  4. Si te ofrece 'Mantener la sesion iniciada', acéptalo.")
    print(f"  Esperando hasta {args.timeout_minutes} min a que entres en tu cuenta...")
    print("=" * 72)

    ok = False
    with sync_playwright() as pw:
        context = launch_context(pw, config)
        page = context.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo abrir {LOGIN_URL}: {e}")

        deadline = time.time() + args.timeout_minutes * 60
        last_note = 0
        while time.time() < deadline:
            try:
                if _is_logged_in(page):
                    ok = True
                    break
            except Exception as e:  # noqa: BLE001
                print(f"\nLa ventana/pagina dejo de responder: {e}")
                break
            # Aviso de progreso cada minuto.
            remaining = int(deadline - time.time())
            if remaining // 60 != last_note:
                last_note = remaining // 60
                print(f"  ...esperando login ({remaining // 60} min restantes)")
            time.sleep(3)

        if ok:
            # Visitar Jobs ya autenticado para consolidar cookies de ese subdominio.
            try:
                page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=90000)
                time.sleep(4)
            except Exception:  # noqa: BLE001
                pass
            save_storage_state(context)
            print("\nOK: sesion iniciada y guardada en auth/storage_state.json")
            print("El perfil persistente tambien queda listo.")
            print('Ahora puedes lanzar: python -m src.main --role "Data Analyst" '
                  '--city Madrid --append')
        else:
            print(f"\nFALLO: no se detecto sesion iniciada en {args.timeout_minutes} min.")

        close_context(context)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
