from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from scraper.config import (
    CIUDADES,
    KEYWORDS,
    MAX_PAGES,
    BASE_URL,
    LOG_DIR,
    RATE_LIMIT_MIN,
    RATE_LIMIT_MAX,
    CAPTCHA_TIMEOUT,
    CAPTCHA_MAX_ATTEMPTS,
    ROBOTS_DISALLOWED,
)
from scraper.browser import get_browser_context
from scraper.navigator import (
    detect_captcha,
    handle_captcha,
    slow_scroll_to_bottom,
    respectful_sleep,
    auto_login,
)
from scraper.parser import parse_listing
from scraper.models import Offer
from scraper.storage import save_all
from scraper.state import RunState
from scraper.notifier import notify

load_dotenv()

# record=True permite volcar el log completo de la run a data/logs/.
console = Console(record=True)

EMPTY_RESULTS_MARKERS = ("Ningún resultado", "0 ofertas")
CARD_MARKER = "ij-OfferList-offerCardItem"


@dataclass
class RunResult:
    offers: list[Offer] = field(default_factory=list)
    blocked: bool = False
    skipped_total: int = 0
    incidencias: list[str] = field(default_factory=list)
    log_path: Path | None = None


def build_search_url(ciudad: dict[str, str], keyword: str, pagina: int) -> str:
    slug = ciudad["slug"].strip("/")
    kw = quote(keyword.strip().lower(), safe="+").replace("%20", "+")
    url = f"{BASE_URL}/ofertas-trabajo/{slug}/{kw}"
    if pagina > 1:
        url = f"{url}/{pagina}"
    return url


def _is_valid_results_page(html: str) -> bool:
    return CARD_MARKER in html or any(marker in html for marker in EMPTY_RESULTS_MARKERS)


def _url_is_disallowed(url: str) -> bool:
    return any(u in url for u in ROBOTS_DISALLOWED)


def _setup_log() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"run_{ts}.log"


def _save_all_offers(offers: list[Offer]) -> None:
    if not offers:
        return
    paths = save_all(offers)
    console.log("[bold green]Archivos guardados:[/]")
    for fmt, path_val in paths.items():
        console.log(f"  {fmt}: {path_val}")


def run(
    ciudades: list[dict] | None = None,
    keywords: list[str] | None = None,
    max_pages: int | None = None,
    login: bool = False,
    unattended: bool = False,
) -> RunResult:
    ciudades = ciudades or CIUDADES
    keywords = keywords or KEYWORDS
    max_pages = max_pages or MAX_PAGES

    log_path = _setup_log()
    all_offers: list[Offer] = []
    bloqueos: list[str] = []
    captcha_blocked = False

    state = RunState()
    skipped_total = 0

    console.log(
        f"[bold]Scraper InfoJobs[/] -- "
        f"{len(ciudades)} ciudades x {len(keywords)} keywords x {max_pages} paginas"
        f"{' (desatendido)' if unattended else ''}"
    )
    console.log(f"Log: {log_path}")
    if state.processed_count > 0:
        console.log(
            f"[dim]State cargado: {state.processed_count} ofertas ya procesadas (se saltaran)[/]"
        )

    playwright, context = get_browser_context()
    page = context.new_page()

    if login:
        auto_login(page)

    try:
        for ciudad in ciudades:
            nombre = ciudad["nombre"]
            ciudad_buscada = f"{nombre} ({ciudad['provincia']})"

            for kw in keywords:
                console.log(
                    f"[bold cyan]{ciudad_buscada}[/] · keyword = [green]{kw}[/]"
                )
                zero_count = 0
                max_consecutive_zeros = 3

                for p in range(1, max_pages + 1):
                    if zero_count >= max_consecutive_zeros:
                        console.log(f"  [dim]{max_consecutive_zeros} pags vacias, saltando keyword[/]")
                        break

                    url = build_search_url(ciudad, kw, p)

                    if _url_is_disallowed(url):
                        bloqueos.append(f"robots: {url}")
                        continue

                    for attempt in range(2):
                        try:
                            console.log(f"  [dim]Pagina {p}/{max_pages}[/]")
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            respectful_sleep(2, 4)

                            if detect_captcha(page):
                                bloqueos.append(f"captcha: {ciudad_buscada} p.{p}")
                                if unattended:
                                    console.log(
                                        "  [red]CAPTCHA en modo desatendido; abortando run[/]"
                                    )
                                    captcha_blocked = True
                                    break
                                if not handle_captcha(
                                    page,
                                    max_attempts=CAPTCHA_MAX_ATTEMPTS,
                                    timeout=CAPTCHA_TIMEOUT,
                                ):
                                    console.log(
                                        "  [red]CAPTCHA no resuelto; abortando run[/]"
                                    )
                                    captcha_blocked = True
                                    break

                            slow_scroll_to_bottom(page)
                            respectful_sleep(1, 2)

                            html = page.content()
                            if not _is_valid_results_page(html):
                                console.log(
                                    "  [red]Respuesta sin estructura de resultados[/]"
                                )
                                bloqueos.append(
                                    f"invalid: {ciudad_buscada} p.{p}"
                                )
                                zero_count = max_consecutive_zeros
                                break
                            offers = parse_listing(html, nombre, kw, p)
                            new_offers = [
                                o for o in offers
                                if not state.is_processed(o.id_oferta)
                            ]
                            skipped = len(offers) - len(new_offers)
                            skipped_total += skipped
                            for o in new_offers:
                                state.mark(o.id_oferta)
                            all_offers.extend(new_offers)

                            if len(new_offers) == 0:
                                zero_count += 1
                                console.log(f"  [dim]0 ofertas nuevas ({zero_count}/{max_consecutive_zeros})[/]")
                            else:
                                zero_count = 0
                                msg = f"  [green][+][/] {len(new_offers)} ofertas"
                                if skipped > 0:
                                    msg += f" ([dim]{skipped} ya scrapeadas[/])"
                                console.log(msg)

                            if p == 1 and offers:
                                fixture_path = (
                                    Path(__file__).resolve().parent.parent
                                    / "tests" / "fixtures"
                                    / f"listing_{nombre.lower()}.html"
                                )
                                fixture_path.parent.mkdir(parents=True, exist_ok=True)
                                fixture_path.write_text(html, encoding="utf-8")

                            break  # success, exit retry loop

                        except Exception as e:
                            err_str = str(e)
                            if "Connection closed" in err_str:
                                console.log("  [red]Conexion perdida, reintentando...[/]")
                                try:
                                    page.close()
                                except Exception:
                                    pass
                                try:
                                    context.close()
                                    playwright.stop()
                                except Exception:
                                    pass
                                playwright, context = get_browser_context()
                                page = context.new_page()
                                respectful_sleep(5, 8)
                                if attempt == 1:
                                    bloqueos.append(f"conn: {ciudad_buscada} p.{p}")
                            else:
                                console.log(f"  [red]Error[/]: {err_str[:80]}")
                                bloqueos.append(f"error: {ciudad_buscada} p.{p}")
                                break

                    if captcha_blocked:
                        break

                    respectful_sleep(RATE_LIMIT_MIN, RATE_LIMIT_MAX)

                if captcha_blocked:
                    break

                _save_all_offers(all_offers)
                state.save()

            if captcha_blocked:
                break
    finally:
        try:
            context.close()
            playwright.stop()
        except Exception:
            pass

    seen_ids: set[str] = set()
    unique_offers: list[Offer] = []
    for o in all_offers:
        if o.id_oferta not in seen_ids:
            seen_ids.add(o.id_oferta)
            unique_offers.append(o)

    console.line()
    console.log(
        f"[bold]Total ofertas:[/] {len(all_offers)} (unicas: {len(unique_offers)})"
    )
    if skipped_total > 0:
        console.log(
            f"[dim]Ofertas ya scrapeadas (saltadas con state.json): {skipped_total}[/]"
        )
    if bloqueos:
        console.log(f"[bold red]Incidencias:[/] {len(bloqueos)}")

    _save_all_offers(unique_offers)
    state.save()
    return RunResult(
        offers=unique_offers,
        blocked=captcha_blocked,
        skipped_total=skipped_total,
        incidencias=bloqueos,
        log_path=log_path,
    )


def main() -> None:
    import argparse

    parser_cfg = argparse.ArgumentParser(description="InfoJobs Job Scraper")
    parser_cfg.add_argument("--ciudades", type=str, default=None)
    parser_cfg.add_argument("--keywords", type=str, default=None)
    parser_cfg.add_argument("--paginas", type=int, default=None)
    parser_cfg.add_argument("--login", action="store_true",
                            help="Iniciar sesion en InfoJobs antes de scrapear")
    parser_cfg.add_argument("--unattended", action="store_true",
                            help="Modo desatendido: no espera resolucion manual "
                                 "de CAPTCHA; aborta y avisa")

    args = parser_cfg.parse_args()

    ciudades = CIUDADES
    if args.ciudades:
        slugs = [s.strip().lower() for s in args.ciudades.split(",")]
        ciudades = [
            c for c in CIUDADES
            if c["slug"].split("/")[-1] in slugs or c["nombre"].lower() in slugs
        ]

    keywords = KEYWORDS
    if args.keywords:
        keywords = [s.strip().lower() for s in args.keywords.split(",")]

    max_pages = args.paginas or MAX_PAGES

    result = run(
        ciudades=ciudades,
        keywords=keywords,
        max_pages=max_pages,
        login=args.login,
        unattended=args.unattended,
    )
    offers = result.offers

    table = Table(title="Resumen")
    table.add_column("Metrica", style="cyan")
    table.add_column("Valor", style="green")
    table.add_row("Total ofertas", str(len(offers)))
    table.add_row("Ciudades", str(len(ciudades)))
    table.add_row("Keywords", str(len(keywords)))
    table.add_row("Paginas", str(max_pages))
    table.add_row("Incidencias", str(len(result.incidencias)))
    console.print(table)

    blocked_flag = "true" if result.blocked else "false"
    console.print(
        f"RESULT total={len(offers)} incidencias={len(result.incidencias)} "
        f"blocked={blocked_flag}"
    )

    if result.blocked:
        notify(
            f"InfoJobs BLOQUEADO por CAPTCHA. ofertas={len(offers)} "
            f"incidencias={len(result.incidencias)}"
        )
        console.log("[bold red]Run abortada por CAPTCHA.[/]")
        exit_code = 2
    elif not offers:
        console.log("[bold yellow]No se obtuvieron ofertas nuevas.[/]")
        exit_code = 1
    else:
        exit_code = 0

    # Unico save_text: esta version de rich limpia el buffer en cada llamada.
    if result.log_path:
        try:
            console.save_text(str(result.log_path))
        except Exception:
            pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
