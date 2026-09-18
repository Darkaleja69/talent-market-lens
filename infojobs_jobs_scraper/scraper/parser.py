from bs4 import BeautifulSoup, Tag
from datetime import date
import re
from scraper.config import BASE_URL
from scraper.models import Offer

MONTH_MAP_ES: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return el.get_text(" ", strip=True)


def _href(el: Tag | None) -> str:
    if el is None:
        return ""
    href: str = str(el.get("href") or "")
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/") and not href.startswith("//"):
        href = BASE_URL + href
    return href


def _extract_offer_id_from_h2(h2: Tag | None) -> str:
    if h2 is None:
        return ""
    h2_id: str = str(h2.get("id") or "")
    if h2_id.startswith("job-title-"):
        return h2_id[len("job-title-"):]
    return ""


def _extract_offer_id_from_url(url: str) -> str:
    m = re.search(r"/of-i([a-f0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/em-i([a-zA-Z0-9_\-]+)", url)
    if m:
        return m.group(1)
    return url


def _parse_relative_date(text: str) -> date | None:
    text = text.strip().lower()
    today = date.today()
    m = re.match(
        r"hace\s+(\d+)\s*(minutos?|horas?|dias|días?|semanas?|meses?|m|h|d|s)?",
        text,
    )
    if m:
        from datetime import timedelta
        num = int(m.group(1))
        unit = (m.group(2) or "dias").rstrip("s")
        if unit in ("m", "minuto"):
            return today - timedelta(minutes=num)
        if unit in ("h", "hora"):
            return today - timedelta(hours=num)
        if unit == "s":
            return today - timedelta(seconds=num)
        if unit == "semana":
            return today - timedelta(weeks=num)
        return today - timedelta(days=num)
    months_re = "|".join(MONTH_MAP_ES)
    m = re.match(rf"(\d+)\s+(?:de\s+)?({months_re})", text)
    if m:
        day = int(m.group(1))
        month = MONTH_MAP_ES[m.group(2)]
        return date(today.year, month, day)
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_salary(text: str) -> tuple[int | None, int | None, str | None, str | None]:
    if not text or "no disponible" in text.lower():
        return None, None, None, None
    text = text.strip()
    numbers = re.findall(r"[\d.]+(?:\s*[,.]?\s*\d+)*", text)
    raw_nums = [int(re.sub(r"[^\d]", "", n)) for n in numbers]
    min_sal = min(raw_nums) if raw_nums else None
    max_sal = max(raw_nums) if len(raw_nums) > 1 else None
    moneda = "EUR" if "€" in text else None
    periodo = None
    if "año" in text.lower() or "anual" in text.lower():
        periodo = "anual"
    elif "mes" in text.lower():
        periodo = "mensual"
    elif "día" in text.lower() or "dia" in text.lower():
        periodo = "diario"
    elif "hora" in text.lower():
        periodo = "por_hora"
    return min_sal, max_sal, moneda, periodo


def parse_listing(html: str, ciudad: str, keyword: str, pagina: int) -> list[Offer]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("li.ij-List-item.ij-OfferList-offerCardItem")
    offers: list[Offer] = []

    for card in cards:
        try:
            card_el: Tag = card
            title_h2 = card_el.select_one("h2.ij-OfferCardContent-description-title")
            title_link = card_el.select_one("a.ij-OfferCardContent-description-link")
            if title_h2 is None or title_link is None:
                # Anuncios, banners y campanas de empresas sin oferta real
                continue
            title_span = card_el.select_one(".ij-OfferCardContent-description-title-link")
            company_link = card_el.select_one(
                "h3.ij-OfferCardContent-description-subtitle a.ij-OfferCardContent-description-subtitle-link"
            )
            city_span = card_el.select_one(".ij-OfferCardContent-description-list-item-truncate")
            date_tag = card_el.select_one("[data-testid='sincedate-tag']")
            salary_info = card_el.select_one(".ij-OfferCardContent-description-salary-info")
            salary_no = card_el.select_one(".ij-OfferCardContent-description-salary-no-information")
            desc_p = card_el.select_one("p.ij-OfferCardContent-description-description")
            meta_lists = card_el.select("ul.ij-OfferCardContent-description-list")

            offer_id = _extract_offer_id_from_h2(title_h2) or _extract_offer_id_from_url(
                _href(title_link)
            )
            titulo = _text(title_span)
            empresa = _text(company_link)
            url_oferta = _href(title_link)

            ciudad_detectada = _text(city_span) if city_span else ciudad
            provincia_detectada = ""

            fecha_pub = _parse_relative_date(_text(date_tag)) if date_tag else None

            salario_raw = _text(salary_info) if salary_info else (_text(salary_no) if salary_no else None)
            if salario_raw and "no disponible" in salario_raw.lower():
                salario_raw = None
            sal_min, sal_max, moneda, periodo = _parse_salary(salario_raw or "")

            modalidad = None
            for ul_el in meta_lists:
                lis = ul_el.select("li")
                if len(lis) >= 2:
                    for li_el in lis[1:2]:
                        txt = _text(li_el)
                        if any(w in txt.lower() for w in ["presencial", "remoto", "teletrabajo", "híbrido", "hibrido"]):
                            modalidad = txt
                            break
                if modalidad:
                    break

            tipo_contrato = None
            jornada = None
            bottom_uls = meta_lists[-2:]
            for ul_el in bottom_uls:
                lis = ul_el.select("li")
                for li_el in lis:
                    txt = _text(li_el)
                    if any(w in txt.lower() for w in ["indefinido", "temporal", "contrato", "autónomo", "prácticas", "practicas"]):
                        if not tipo_contrato:
                            tipo_contrato = txt
                    elif any(w in txt.lower() for w in ["jornada completa", "jornada parcial", "full-time", "part-time"]):
                        if not jornada:
                            jornada = txt

            descripcion_snippet = _text(desc_p) if desc_p else None

            offer = Offer(
                id_oferta=offer_id,
                titulo=titulo or "Desconocido",
                empresa=empresa,
                ciudad=ciudad_detectada,
                provincia=provincia_detectada,
                fecha_publicacion=fecha_pub,
                categoria=None,
                salario_raw=salario_raw,
                salario_min=sal_min,
                salario_max=sal_max,
                moneda=moneda,
                periodo=periodo,
                jornada=jornada,
                tipo_contrato=tipo_contrato,
                experiencia_min=None,
                modalidad=modalidad,
                descripcion_snippet=descripcion_snippet,
                url_oferta=url_oferta,
                ciudad_buscada=ciudad,
                keyword_buscada=keyword,
                pagina=pagina,
            )
            offers.append(offer)
        except Exception:
            continue

    return offers
