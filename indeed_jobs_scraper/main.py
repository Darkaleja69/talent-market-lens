"""Scraper de Indeed - entrada principal (CLI).

Uso tipico (prototipo: Espana, Madrid/Barcelona/Bilbao, 2 paginas, termino "data"):

    python main.py
    python main.py --country ES --cities Madrid,Barcelona,Bilbao --terms "data,python" --pages 2

Filtros de refinamiento:
    python main.py --terms "data,python" --fromage 7 --remote --job-type fulltime

Otros paises preparados (necesitan proxies para uso repetido):
    python main.py --country IE --cities Dublin --terms "data" --pages 2
    python main.py --country CH --cities Zurich,Geneva --terms "data" --pages 2
    python main.py --country NL --cities Amsterdam --terms "data" --pages 2
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from scraper.config import COUNTRIES, DEFAULT_COUNTRY, DEFAULT_PAGES, DEFAULT_TERM, MAX_SERPS_PER_RUN
from scraper.runner import ScraperRunner


def setup_logging(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = output_dir / f"log_{ts}.log"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    try:
        _ = sys.stdout.fileno()
        handlers.append(logging.StreamHandler(sys.stdout))
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    return log_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scraper de Indeed (patchright + stealth). Evita CAPTCHAs con scrolling humano y delays largos."
    )
    p.add_argument(
        "--country",
        default=DEFAULT_COUNTRY,
        choices=list(COUNTRIES.keys()),
        help=f"Codigo de pais (default: {DEFAULT_COUNTRY}). Disponibles: {', '.join(COUNTRIES)}.",
    )
    p.add_argument(
        "--cities",
        default=None,
        help="Lista de ciudades separadas por comas. Si se omite, usa las del pais.",
    )
    p.add_argument("--term", default=DEFAULT_TERM, help=f'Termino de busqueda unico (default: "{DEFAULT_TERM}"). Usa --terms para varios.')
    p.add_argument(
        "--terms",
        default=None,
        help="Lista de terminos de busqueda separados por comas (ej: 'data,python,data engineer'). Sobrescribe --term.",
    )
    p.add_argument("--pages", type=int, default=DEFAULT_PAGES, help=f"Numero de paginas por ciudad (default: {DEFAULT_PAGES}).")
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Resultados por pagina (parametro limit de Indeed). >0 pide mas resultados en una sola pagina y evita el login que Indeed exige para paginar (ej: --limit 50). 0 = no anadir parametro.",
    )
    p.add_argument(
        "--fromage",
        type=int,
        default=0,
        help="Filtro de antiguedad en dias (0=sin filtro). Ej: --fromage 7 para ofertas de los ultimos 7 dias.",
    )
    p.add_argument(
        "--remote",
        action="store_true",
        help="Filtrar solo ofertas remotas o hibridas.",
    )
    p.add_argument(
        "--job-type",
        default="",
        help="Tipo de empleo (param jt= de Indeed). Valores: fulltime, parttime, contract, temporary, commission, internship.",
    )
    p.add_argument(
        "--login",
        action="store_true",
        help="Login asistido: abre el navegador en la pagina de login de Indeed para que te loguees a mano (resolviendo el CAPTCHA). Necesario para paginar mas alla de la pagina 1, que Indeed bloquea con login. La sesion se guarda para reutilizarla.",
    )
    p.add_argument(
        "--use-chrome-profile",
        action="store_true",
        help="Usa tu perfil REAL de Chrome (donde ya tienes Google iniciado). Asi 'Continuar con Google' en Indeed funciona con un clic. REQUIERE cerrar todas las ventanas de Chrome antes de ejecutar.",
    )
    p.add_argument(
        "--cdp",
        type=int,
       default=0,
        metavar="PORT",
        help="Conecta a un Chrome que TU has lanzado a mano con --remote-debugging-port=PORT. Asi todos los botones de login (incluido Google) funcionan porque Chrome no fue lanzado por Playwright. Ej: --cdp 9222.",
    )
    p.add_argument(
        "--enrich",
        action="store_true",
        help="Enriquece cada oferta con su descripcion completa y condiciones (beneficios, jornada, etc.) haciendo clic en cada job card del panel izquierdo y cosechando el panel derecho. Mas lento (~3s por oferta) pero obtiene datos completos.",
    )
    p.add_argument(
        "--enrich-rate",
        type=float,
        default=0.0,
        help="Fraccion de ofertas a enriquecer (0.0-1.0). Ej: 0.3 = 30%%. Menos clicks = menos riesgo CAPTCHA. Ideal para nightly.",
    )
    p.add_argument(
        "--enrich-max",
        type=int,
        default=0,
        metavar="N",
        help="Tope de clics de enriquecimiento por SERP (0=sin tope). El resto de ofertas sin descripcion se rellena desde la cache persistente (descriptions_cache.json) sin clics. Mas bajo = mas seguro.",
    )
    p.add_argument(
        "--pause-every",
        type=int,
        default=0,
        metavar="N",
        help="Pausa larga cada N SERPs para reducir riesgo CAPTCHA (0=no pausar). Ej: --pause-every 15.",
    )
    p.add_argument(
        "--pause-min",
        type=float,
        default=5.0,
        metavar="MINS",
        help="Minutos minimos de pausa larga (default: 5).",
    )
    p.add_argument(
        "--pause-max",
        type=float,
        default=8.0,
        metavar="MINS",
        help="Minutos maximos de pausa larga (default: 8).",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Directorio de salida (default: ./output).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    output_dir = Path(args.output) if args.output else (project_root / "output")
    log_path = setup_logging(output_dir)

    country = COUNTRIES[args.country]
    cities = [c.strip() for c in args.cities.split(",")] if args.cities else list(country.cities)
    terms = [t.strip() for t in args.terms.split(",")] if args.terms else [args.term]

    total_serps = len(cities) * args.pages * len(terms)
    filtros = []
    if args.fromage:
        filtros.append(f"fromage={args.fromage}")
    if args.remote:
        filtros.append("remote")
    if args.job_type:
        filtros.append(f"jt={args.job_type}")
    filtro_str = f" filtros=({', '.join(filtros)})" if filtros else ""
    print(f"Indeed scraper: pais={args.country} ciudades={cities} terminos={terms} paginas={args.pages}{filtro_str}")
    print(f"SERPs totales: {total_serps} (limite sin proxy: {MAX_SERPS_PER_RUN})")
    print(f"Log: {log_path}")
    print(f"Output: {output_dir}")
    print("-" * 60)

    if total_serps > MAX_SERPS_PER_RUN:
        print(f"[ERROR] {total_serps} SERPs superan el limite de {MAX_SERPS_PER_RUN} sin proxy.", file=sys.stderr)
        return 2

    try:
        runner = ScraperRunner(
            args.country, cities, terms, args.pages, output_dir,
            limit=args.limit, login=args.login,
            use_chrome_profile=args.use_chrome_profile,
            cdp_port=args.cdp, enrich=args.enrich,
            fromage=args.fromage, remote=args.remote, job_type=args.job_type,
            pause_every=args.pause_every, pause_min=args.pause_min,
            pause_max=args.pause_max,
            enrich_rate=args.enrich_rate, enrich_max=args.enrich_max,
        )
        offers = runner.run()
        if getattr(runner, "captcha_aborted", False):
            print(
                "\n[!] Bloqueado por challenge anti-bot. NO reintentes esta noche:"
                "\n    espera al menos 24h o cambia de red. Exit code 3.\n",
                file=sys.stderr,
            )
            return 3
    except KeyboardInterrupt:
        print("\n[interrumpido por usuario]")
        return 130
    except Exception as e:
        logging.getLogger(__name__).exception("error fatal: %s", e)
        return 1

    print("-" * 60)
    print(f"Scrape completado: {len(offers)} ofertas unicas.")
    if offers:
        print(f"Archivos en {output_dir}:")
        stem = runner.timestamp
        for ext in ("parquet", "jsonl", "json", "csv", "xlsx"):
            f = output_dir / f"indeed_jobs_{stem}.{ext}"
            if f.exists():
                print(f"  - {f.name}")
        meta_f = output_dir / f"scrape_metadata_{stem}.json"
        if meta_f.exists():
            print(f"  - {meta_f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
