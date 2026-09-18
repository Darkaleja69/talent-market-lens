"""Segmentacion heuristica de la descripcion de oferta en secciones logicas.

LinkedIn NO expone las secciones (funciones, requisitos, beneficios) de forma
estructurada en el HTML. Las cabeceras son <strong> inline o <h3> dentro del
bloque [data-testid="expandable-text-box"].

Este modulo:
  - Para HTML: busca <strong>/<h3> que coincidan con cabeceras conocidas y
    extrae el contenido entre ellas.
  - Para texto plano (Apify): busca lineas que coincidan con patrones de
    cabecera (case-insensitive, normalizando acentos).

Devuelve un dict con 5 keys: role_summary, company_description,
responsibilities, requirements, benefits.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# --- Cabeceras bilingue ES/EN para cada seccion ---
# Orden: primero las mas especificas para evitar falsos positivos.
# Cada entrada es (regex compilado, seccion_destino).
# El texto se normaliza (lower + strip diacritics) antes de matchear.

SECTION_HEADERS: list[tuple[re.Pattern, str]] = [
    # -- company_description (intro corporativa) --
    (re.compile(r"company\s*description", re.I), "company_description"),
    (re.compile(r"about\s+the\s+company|about\s+us|who\s+we\s+are", re.I), "company_description"),
    (re.compile(r"sobre\s+(la\s+)?empresa|sobre\s+nosotros|acerca\s+de", re.I), "company_description"),
    (re.compile(r"qui[eé]nes\s+somos", re.I), "company_description"),
    (re.compile(r"con[oó]cenos|nuestra\s+empresa|el\s+grupo", re.I), "company_description"),
    (re.compile(r"our\s+company|the\s+company", re.I), "company_description"),

    # -- responsibilities (funciones / tareas) --
    (re.compile(r"entre\s+las\s+funciones\s+del\s+puesto", re.I), "responsibilities"),
    (re.compile(r"funciones\s*(y\s+responsabilidades)?", re.I), "responsibilities"),
    (re.compile(r"responsabilidades", re.I), "responsibilities"),
    (re.compile(r"qu[eé]\s+har[aá]s\s*(en\s+este\s+rol|con\s+nosotros|en\s+tu\s+d[ií]a\s+a\s+d[ií]a)?", re.I), "responsibilities"),
    (re.compile(r"c[uó]mo\s+ser[aá]\s+tu\s+d[ií]a\s+a\s+d[ií]a", re.I), "responsibilities"),
    (re.compile(r"cu[aá]l\s+ser[aá]\s+tu\s+rol", re.I), "responsibilities"),
    (re.compile(r"tus\s+tareas|tu\s+rol|tu\s+misi[oó]n", re.I), "responsibilities"),
    (re.compile(r"your\s+mission|the\s+journey", re.I), "responsibilities"),
    (re.compile(r"key\s+responsibilities\s*(include)?", re.I), "responsibilities"),
    (re.compile(r"main\s+responsibilities", re.I), "responsibilities"),
    (re.compile(r"role\s+overview|the\s+role|about\s+the\s+role", re.I), "responsibilities"),
    (re.compile(r"what\s+you.{1,15}(do|will\s+do)", re.I), "responsibilities"),
    (re.compile(r"job\s+description(?!\s*$)", re.I), "responsibilities"),
    (re.compile(r"descripci[oó]n\s+del\s+puesto", re.I), "responsibilities"),
    (re.compile(r"funciones\s+del\s+puesto", re.I), "responsibilities"),
    (re.compile(r"the\s+opportunity|more\s+about\s+the\s+opportunity", re.I), "responsibilities"),
    (re.compile(r"\bresponsibilities\b", re.I), "responsibilities"),

    # -- requirements (requisitos / perfil buscado) --
    (re.compile(r"requisitos\s*(imprescindibles|m[ií]nimos|t[eé]cnicos|indispensables)?", re.I), "requirements"),
    (re.compile(r"qu[eé]\s+buscamos|qu[eé]\s+estamos\s+buscando|perfil\s+buscado", re.I), "requirements"),
    (re.compile(r"qu[eé]\s+esperamos\s+de\s+ti", re.I), "requirements"),
    (re.compile(r"qu[eé]\s+valoramos\s+de\s+tu\s+candidatura", re.I), "requirements"),
    (re.compile(r"a\s+qui[eé]n\s+buscamos", re.I), "requirements"),
    (re.compile(r"se\s+requiere|requerido", re.I), "requirements"),
    (re.compile(r"se\s+valorar[aá]", re.I), "requirements"),
    (re.compile(r"conocimientos", re.I), "requirements"),
    (re.compile(r"formaci[oó]n", re.I), "requirements"),
    (re.compile(r"experiencia", re.I), "requirements"),
    (re.compile(r"deseable|valorable|te\s+suman\s+puntos", re.I), "requirements"),
    (re.compile(r"required\s+skills\s*[&and]?\s*qualifications", re.I), "requirements"),
    (re.compile(r"what\s+you.{1,15}(bring|need|have)", re.I), "requirements"),
    (re.compile(r"what\s+we.{1,10}looking\s+for", re.I), "requirements"),
    (re.compile(r"qualifications?", re.I), "requirements"),
    (re.compile(r"minimum\s+qualifications|preferred\s+qualifications", re.I), "requirements"),
    (re.compile(r"skills?\s*[&and]?\s*experience|knowledge", re.I), "requirements"),
    (re.compile(r"soft\s+skills", re.I), "requirements"),
    (re.compile(r"technical\s+skills", re.I), "requirements"),
    (re.compile(r"si\s+has\s+estudiado", re.I), "requirements"),
    (re.compile(r"educa(?:tion|ci[oó]n)", re.I), "requirements"),
    (re.compile(r"your\s+profile|your\s+background|candidate\s+profile", re.I), "requirements"),
    (re.compile(r"\brequirements?\b", re.I), "requirements"),

    # -- benefits (lo que ofrecen / condiciones) --
    (re.compile(r"lo\s+que\s+ofrecemos", re.I), "benefits"),
    (re.compile(r"qu[eé]\s+(te\s+)?ofrecemos", re.I), "benefits"),
    (re.compile(r"qu[eé]\s+podemos\s+ofrecerte", re.I), "benefits"),
    (re.compile(r"qu[eé]\s+(te\s+)?vas\s+a\s+encontrar\s+aqu[ií]", re.I), "benefits"),
    (re.compile(r"ofrecemos", re.I), "benefits"),
    (re.compile(r"qu[eé]\s+te\s+ofrecemos", re.I), "benefits"),
    (re.compile(r"por\s+qu[eé]\s+(es\s+una\s+gran\s+oportunidad|trabajar\s+con\s+nosotros)", re.I), "benefits"),
    (re.compile(r"te\s+sumas\s+al\s+viaje", re.I), "benefits"),
    (re.compile(r"benefits?\s*[&and]?\s*perks?", re.I), "benefits"),
    (re.compile(r"what\s+we\s+offer", re.I), "benefits"),
    (re.compile(r"perks?\s*[&and]?\s*benefits?", re.I), "benefits"),
    (re.compile(r"compensation|salary\s*[&and]?\s*benefits", re.I), "benefits"),
    (re.compile(r"why\s+(join\s+us|work\s+with\s+us|us)", re.I), "benefits"),
    (re.compile(r"our\s+offer|what.{1,10}in\s+it\s+for\s+you", re.I), "benefits"),
    (re.compile(r"what\s+you.{1,10}(get|gain|enjoy)", re.I), "benefits"),
    (re.compile(r"condiciones\s+laborales|remuneraci[oó]n", re.I), "benefits"),
    (re.compile(r"\bbenefits?\b", re.I), "benefits"),
    (re.compile(r"incorporaci[oó]n|ubicaci[oó]n|horario|duraci[oó]n", re.I), "benefits"),
]

SECTION_KEYS = ("role_summary", "company_description", "responsibilities", "requirements", "benefits")


def _normalize(text: str) -> str:
    """Strip accents, lower, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_text.lower().strip()


def _match_section(text: str) -> Optional[str]:
    """Devuelve la seccion (key) a la que pertenece una cabecera, o None."""
    norm = _normalize(text)
    for pattern, section in SECTION_HEADERS:
        if pattern.search(norm):
            return section
    return None


def _extract_headers_text(lines: list[str]) -> list[tuple[int, str, str]]:
    """Encuentra cabeceras en lineas de texto plano.

    Devuelve [(indice_linea, texto_linea, seccion_destino), ...].
    Se considera cabecera una linea corta (<70 chars) que matchea
    SECTION_HEADERS y tiene ciertas caracteristicas (dos puntos, mayusculas,
    o patron ¿...?).
    """
    headers: list[tuple[int, str, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 70:
            continue
        section = _match_section(stripped)
        if section:
            headers.append((idx, stripped, section))
    return headers


def split_description_sections(content: str, source: str = "html") -> dict[str, str]:
    """Segmenta la descripcion en 5 secciones logicas.

    Args:
        content: HTML o texto plano de la descripcion.
        source: "html" (usa <strong>/<h3> como cabeceras) o "text" (lineas).

    Returns:
        Dict con keys: role_summary, company_description, responsibilities,
        requirements, benefits. Secciones no detectadas quedan vacias.
    """
    result: dict[str, str] = {k: "" for k in SECTION_KEYS}

    if not content or not content.strip():
        return result

    if source == "html":
        return _split_html(content, result)
    else:
        return _split_text(content, result)


def _split_html(html: str, result: dict[str, str]) -> dict[str, str]:
    """Segmenta HTML usando <strong> y <h3> como detectores de cabecera.

    Construye marcadores: posicion de inicio de cada cabecera.
    Luego extrae texto limpio entre marcadores.
    """
    # Limpiar scripts y estilos antes del parseo
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.I)

    # Encontrar todas las cabeceras <strong>...</strong> o <h3>...</h3>
    # que matchean SECTION_HEADERS
    markers: list[tuple[int, str, re.Match]] = []  # (pos_inicio, seccion, match)

    # <strong>...</strong>
    strong_pat = re.compile(
        r"<strong[^>]*>(.*?)</strong>",
        re.IGNORECASE | re.DOTALL,
    )
    for m in strong_pat.finditer(html):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        inner_clean = re.sub(r"[^\w\s]", "", inner).strip()
        if len(inner_clean) > 2 and len(inner_clean) < 80:
            section = _match_section(inner)
            if section:
                markers.append((m.start(), section, m))

    # <h3>...</h3>
    h3_pat = re.compile(r"<h3[^>]*>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
    for m in h3_pat.finditer(html):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        inner_clean = re.sub(r"[^\w\s]", "", inner).strip()
        if len(inner_clean) > 2 and len(inner_clean) < 80:
            section = _match_section(inner)
            if section:
                markers.append((m.start(), section, m))

    # <h2>...</h2>
    h2_pat = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
    for m in h2_pat.finditer(html):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        inner_clean = re.sub(r"[^\w\s]", "", inner).strip()
        if len(inner_clean) > 2 and len(inner_clean) < 80:
            section = _match_section(inner)
            if section:
                markers.append((m.start(), section, m))

    markers.sort(key=lambda x: x[0])

    if not markers:
        result["role_summary"] = _strip_html(html)
        return result

    # Eliminar marcadores redundantes (misma seccion muy cercana)
    deduped: list[tuple[int, str, int]] = []  # (pos, seccion, end_pos)
    last_section: str | None = None
    last_pos = -1
    for pos, section, m in markers:
        if section == last_section and pos - last_pos < 30:
            continue  # mismo <strong> duplicado
        deduped.append((pos, section, m.end()))
        last_section = section
        last_pos = pos

    # role_summary = desde inicio hasta primer marcador
    role_html = html[:deduped[0][0]]
    role_text = _strip_html(role_html)
    if role_text.strip():
        result["role_summary"] = role_text.strip()

    # Secciones intermedias
    for i, (pos, section, end) in enumerate(deduped):
        next_pos = deduped[i + 1][0] if i + 1 < len(deduped) else len(html)
        section_html = html[end:next_pos]
        section_text = _strip_html(section_html).strip()
        if section_text:
            if result.get(section, ""):
                result[section] += "\n\n" + section_text
            else:
                result[section] = section_text

    return result


def _split_text(text: str, result: dict[str, str]) -> dict[str, str]:
    """Segmenta texto plano buscando lineas-cabecera."""
    lines = text.split("\n")
    headers = _extract_headers_text(lines)

    if not headers:
        result["role_summary"] = text.strip()
        return result

    # role_summary: lineas antes de la primera cabecera
    first_idx = headers[0][0]
    summary_lines = lines[:first_idx]
    result["role_summary"] = "\n".join(summary_lines).strip()

    # Secciones intermedias
    for i, (idx, _header_text, section) in enumerate(headers):
        next_idx = headers[i + 1][0] if i + 1 < len(headers) else len(lines)
        content_lines = lines[idx + 1:next_idx]
        content = "\n".join(content_lines).strip()
        if content:
            if result.get(section, ""):
                result[section] += "\n\n" + content
            else:
                result[section] = content

    return result


def _strip_html(html: str) -> str:
    """Convierte HTML a texto plano simple, preservando saltos de <br> y bloques."""
    # Reemplazar <br> y <br/> por saltos
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    # Reemplazar cierre de <p>, <div>, <li> por salto
    text = re.sub(r"</(?:p|div|li|h[1-6]|tr|section|article)>", "\n", text, flags=re.I)
    # Reemplazar <li> por guion
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    # Eliminar resto de tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decodificar entidades HTML comunes
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("&aacute;", "á").replace("&eacute;", "é").replace("&iacute;", "í")
    text = text.replace("&oacute;", "ó").replace("&uacute;", "ú").replace("&ntilde;", "ñ")
    text = text.replace("&Aacute;", "Á").replace("&Eacute;", "É").replace("&Iacute;", "Í")
    text = text.replace("&Oacute;", "Ó").replace("&Uacute;", "Ú").replace("&Ntilde;", "Ñ")
    text = text.replace("&iexcl;", "¡").replace("&iquest;", "¿")
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
