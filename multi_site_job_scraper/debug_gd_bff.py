"""Captura en vivo la respuesta real del BFF GraphQL de Glassdoor.

Uso: python debug_gd_bff.py
Intercepta las respuestas a /graph al cargar un SERP real y guarda el JSON
crudo en data/raw_html/gd_bff_response.json para analizar que campos devuelve
(descripcion, salario, employmentType, etc.).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "data" / "raw_html"
OUT_DIR.mkdir(parents=True, exist_ok=True)

captured: list[dict] = []


def on_response(resp):
    try:
        url = resp.url
        if "/graph" in url or "jobSearchResultsQuery" in url:
            if resp.request.method != "POST":
                return
            body = resp.text()
            captured.append({
                "url": url,
                "status": resp.status,
                "request_post_data": resp.request.post_data,
                "body": body,
            })
            print(f"[capturado] {url} status={resp.status} len={len(body)}")
    except Exception as e:
        print(f"[error captura] {e}")


def main() -> int:
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright

    from src.core.browser import launch_context, close_context, PROFILE_DIR
    from src.glassdoor import serp

    os.environ["HEADLESS"] = "false"

    pw = sync_playwright().start()
    context = launch_context(pw, locale="en-US", timezone="America/New_York",
                             profile_name="glassdoor")
    page = context.new_page()
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(page)
    except ImportError:
        pass

    page.on("response", on_response)

    def wait_cf_clear(pg, max_wait: float = 120.0) -> bool:
        """Espera a que el challenge de Cloudflare se resuelva.

        Intenta hacer clic en el widget Turnstile (iframe de challenges)
        periodicamente, como haria un humano.
        """
        t0 = time.time()
        clicked = False
        while time.time() - t0 < max_wait:
            try:
                title = (pg.title() or "").lower()
                content = (pg.content() or "").lower()
            except Exception:
                title, content = "", ""
            ok = (title and "just a moment" not in title
                  and "moment..." not in title)
            if ok and "humans only" not in content[:5000]:
                return True
            if not clicked:
                try:
                    for frame in pg.frames:
                        if "challenges.cloudflare.com" in (frame.url or ""):
                            try:
                                box = frame.query_selector(
                                    "input[type='checkbox'], .ctp-checkbox-label, "
                                    "#challenge-stage, label")
                                if box:
                                    box.click(timeout=3000)
                                    clicked = True
                                    print("   [cf] click en turnstile")
                            except Exception:
                                pass
                except Exception:
                    pass
            time.sleep(4)
        return False

    def goto_cf(pg, url: str, timeout: int = 60000) -> bool:
        for attempt in range(3):
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except Exception as e:
                print(f"   goto fallo (intento {attempt+1}): {e}")
                time.sleep(5)
                continue
            if wait_cf_clear(pg):
                return True
            print(f"   challenge no resuelto (intento {attempt+1}), recargando...")
            time.sleep(random.uniform(4, 8))
        return False

    # 1) Homepage para cookies/CSRF
    print("-> homepage")
    goto_cf(page, "https://www.glassdoor.com", timeout=45000)
    print(f"   homepage url={page.url[:80]}")
    time.sleep(random.uniform(4, 6))

    html = page.content()
    (OUT_DIR / "gd_home.html").write_text(html, encoding="utf-8")

    # 2) SERP real
    print("-> SERP")
    serp_url = "https://www.glassdoor.com/Job/jobs.htm?sc.keyword=Data+Analyst&fromAge=7"
    goto_cf(page, serp_url, timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(random.uniform(3, 5))

    html2 = page.content()
    (OUT_DIR / "gd_serp.html").write_text(html2, encoding="utf-8")

    print(f"URL final: {page.url}")
    print(f"Respuestas capturadas: {len(captured)}")

    if captured:
        # Guardar respuesta cruda completa
        (OUT_DIR / "gd_bff_response.json").write_text(
            json.dumps(captured[-1]["body"], ensure_ascii=False), encoding="utf-8")
        (OUT_DIR / "gd_bff_request.json").write_text(
            json.dumps(captured[-1]["request_post_data"], ensure_ascii=False),
            encoding="utf-8")
        # Analisis rapido del primer jobview
        try:
            rj = json.loads(captured[-1]["body"])
            if isinstance(rj, list):
                rj = rj[0]
            listings = (((rj.get("data") or {}).get("jobListings") or {})
                        .get("jobListings")) or []
            print(f"listings: {len(listings)}")
            if listings:
                jv = listings[0]
                with open(OUT_DIR / "gd_jobview_sample.json", "w",
                          encoding="utf-8") as f:
                    json.dump(jv, f, ensure_ascii=False, indent=2)
                jv2 = jv.get("jobview") or jv
                header = jv2.get("header") or {}
                job = jv2.get("job") or {}
                print("header keys:", sorted(header.keys()))
                print("job keys:", sorted(job.keys()))
                print("description len:", len(str(job.get("description") or "")))
                print("payPeriod:", header.get("payPeriod"))
                print("payCurrency:", header.get("payCurrency"))
                print("salarySource:", header.get("salarySource"))
                print("payPeriodAdjustedPay:", header.get("payPeriodAdjustedPay"))
                print("employmentType:", header.get("employmentType"))
                print("locationName:", header.get("locationName"))
        except Exception as e:
            print(f"[error analisis] {e}")

    # 3) Probar JobDetailQuery por si la descripcion no viene en el SERP
    if captured and listings:
        try:
            rj = json.loads(captured[-1]["body"])
            if isinstance(rj, list):
                rj = rj[0]
            first = ((((rj.get("data") or {}).get("jobListings") or {})
                      .get("jobListings")) or [{}])[0]
            jv2 = first.get("jobview") or first
            listing_id = ((jv2.get("job") or {}).get("listingId")) or ""
            if listing_id:
                print(f"-> JobDetailQuery para listingId={listing_id}")
                from src.glassdoor import bff
                ua = page.evaluate("() => navigator.userAgent")
                token = bff.extract_csrf_token(html)
                print(f"   csrf de homepage: {'OK' if token else 'NO'}")
                desc = bff.fetch_description(context.request,
                                             "https://www.glassdoor.com",
                                             token, listing_id, user_agent=ua)
                print(f"   descripcion obtenida: {len(desc)} chars")
                (OUT_DIR / "gd_detail_desc.txt").write_text(desc or "(vacio)",
                                                            encoding="utf-8")
        except Exception as e:
            print(f"[error JobDetailQuery] {e}")

    close_context(context)
    pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
