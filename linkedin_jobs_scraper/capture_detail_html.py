"""Captura el HTML de una pagina de detalle de oferta autenticada."""
import pathlib
import re
from collections import Counter

from patchright.sync_api import sync_playwright

JOB_URL = "https://www.linkedin.com/jobs/view/4426806347/"

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
    page.goto(JOB_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    html = page.content()
    out = pathlib.Path("data/raw_html/auth_detail.html")
    out.write_text(html, encoding="utf-8")
    print("HTML guardado:", out, len(html), "bytes")
    print("URL:", page.url)
    print("Title:", page.title())

    # Buscar clases relevantes
    classes = re.findall(r'class="([^"]*(?:description|skill|salary|insight|about|detail|job)[^"]*)"', html)
    print("\n--- Top clases relacionadas con detail ---")
    for cls, cnt in Counter(classes).most_common(30):
        print(f"  {cnt:4d}x  {cls[:110]}")

    # Buscar about:company / skills
    for kw in ["show-more-less-html", "jobs-description", "job-details", "skill",
               "salary", "applicants", "workplaceType", "employmentType",
               "seniority", "companyIndustry", "companySize", "employees"]:
        matches = re.findall(rf'class="([^"]*{kw}[^"]*)"', html, re.IGNORECASE)
        if matches:
            print(f"\n--- '{kw}' ({len(matches)} matches) ---")
            for m in Counter(matches).most_common(3):
                print(f"  {m[1]:3d}x  {m[0][:110]}")

    ctx.close()
