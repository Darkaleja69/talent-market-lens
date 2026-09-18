"""Script temporal para capturar el HTML autenticado del SERP de LinkedIn Jobs.

Uso: python capture_auth_html.py
Necesita auth/storage_state.json (login ya hecho).
"""
import pathlib
import re
from collections import Counter

from patchright.sync_api import sync_playwright

ss = pathlib.Path("auth/storage_state.json")
if not ss.exists():
    print("No storage_state. Ejecuta login primero.")
    raise SystemExit(1)

URL = ("https://www.linkedin.com/jobs/search/?keywords=Data+Analyst"
       "&location=Madrid%2C+Spain&geoId=103374081&f_TPR=r2592000")

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(pathlib.Path("auth/profile")),
        headless=False,
        viewport={"width": 1536, "height": 864},
        locale="es-ES",
        timezone_id="Europe/Madrid",
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    html = page.content()
    out = pathlib.Path("data/raw_html/auth_serp_madrid.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print("HTML guardado:", out, len(html), "bytes")
    print("URL final:", page.url)
    print("Title:", page.title())

    # Buscar clases que parezcan tarjetas de job
    classes = re.findall(r'class="([^"]*(?:job|card|result|entity|scaffold)[^"]*)"', html)
    print("\n--- Top clases relacionadas con job/card/result ---")
    for cls, cnt in Counter(classes).most_common(25):
        print(f"  {cnt:4d}x  {cls[:100]}")

    # Buscar data-entity-urn (job IDs)
    urns = re.findall(r'data-entity-urn="([^"]*)"', html)
    print(f"\n--- data-entity-urn: {len(urns)} encontrados ---")
    for u in urns[:5]:
        print(f"  {u}")

    # Buscar jobPosting
    postings = re.findall(r"urn:li:jobPosting:(\d+)", html)
    print(f"\n--- urn:li:jobPosting: {len(postings)} encontrados ---")
    for p in postings[:5]:
        print(f"  {p}")

    # Buscar contenedores de lista
    lists = re.findall(r'class="([^"]*(?:results-list|job-results|search-results|jobs-search)[^"]*)"', html)
    print(f"\n--- Clases de listas ---")
    for cls, cnt in Counter(lists).most_common(10):
        print(f"  {cnt:4d}x  {cls[:100]}")

    ctx.close()
