from pathlib import Path

from playwright.sync_api import sync_playwright
import time

playwright = sync_playwright().start()
context = playwright.chromium.launch_persistent_context(
    user_data_dir=str(Path(__file__).resolve().parent / "data" / "profile"),
    headless=False,
    channel="chrome",
    viewport={"width": 1920, "height": 1080},
    locale="es-ES",
    timezone_id="Europe/Madrid",
)
page = context.new_page()
page.goto("https://www.infojobs.net", wait_until="domcontentloaded", timeout=30000)
time.sleep(5)

inputs = page.locator('input[type="text"], input:not([type])').all()
selects = page.locator("select").all()
buttons = page.locator("button").all()

print("=== INPUTS ===")
for el in inputs:
    print(f"  id={el.get_attribute('id')} name={el.get_attribute('name')} placeholder={el.get_attribute('placeholder')} class={el.get_attribute('class')}")

print("=== SELECTS ===")
for el in selects:
    print(f"  id={el.get_attribute('id')} name={el.get_attribute('name')}")

print("=== BUTTONS ===")
for el in buttons:
    print(f"  id={el.get_attribute('id')} text={el.text_content()[:40]} class={el.get_attribute('class')}")

context.close()
playwright.stop()
print("DONE")
