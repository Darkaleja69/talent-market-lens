from pathlib import Path

from playwright.sync_api import sync_playwright

playwright = sync_playwright().start()
context = playwright.chromium.launch_persistent_context(
    user_data_dir=str(Path(__file__).resolve().parent / "data" / "profile"),
    headless=False,
    channel="chrome",
    viewport={"width": 1920, "height": 1080},
    args=["--start-maximized"],
)
page = context.new_page()

page.goto("https://www.infojobs.net", wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(5000)

values = page.evaluate("""() => {
    const sel = document.querySelector('#of_provincia');
    if (!sel) return 'NOT FOUND';
    const opts = [];
    for (const opt of sel.options) {
        opts.push({value: opt.value, text: opt.textContent});
    }
    return opts;
}""")

print(f"\nFound {len(values) if isinstance(values, list) else 0} options:")
if isinstance(values, list):
    for v in values[:60]:
        print(f"  {v['value']:25s} | {v['text']}")

context.close()
playwright.stop()
