"""Traduccion de los datos al ingles antes de generar el CSV/Parquet unificado.

- Campos categoricos (modalidad, tipo de contrato, nivel) -> diccionario fijo,
  para que los valores sean consistentes entre sitios (Hybrid, Permanent, ...).
- Campos de texto libre (titulo, descripcion, requisitos, ...) -> traduccion
  automatica. Backend por defecto: **Argos Translate** (offline, sin limites de
  red). Fallback: endpoint gratuito de Google (puede devolver 429).
- El texto que ya esta en ingles se deja tal cual (deteccion previa), por lo que
  solo se traducen las ofertas en holandes/aleman/etc.

El resultado se cachea en disco (JSON) para no repetir traducciones.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger("translate")

# Argos/Stanza son muy verbosos a nivel INFO; silenciar para no inundar el log.
for _name in ("argostranslate", "argostranslate.utils", "argostranslate.package",
              "argostranslate.translate", "stanza", "stanza.models"):
    logging.getLogger(_name).setLevel(logging.WARNING)

_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_MAX_CHUNK = 4500  # caracteres por peticion (Google); Argos aguanta parrafos

_CATEGORICAL: dict[str, dict[str, str]] = {
    "work_mode": {
        "hybride": "Hybrid", "hybrid": "Hybrid",
        "werken op locatie": "On-site", "op locatie": "On-site",
        "locatie": "On-site", "on-site": "On-site", "onsite": "On-site",
        "remote": "Remote", "thuiswerken": "Remote", "remote werken": "Remote",
    },
    "employment_type": {
        "vast": "Permanent", "vast contract": "Permanent", "permanent": "Permanent",
        "tijdelijk": "Temporary", "tijdelijk contract": "Temporary",
        "temporary": "Temporary", "freelance": "Freelance", "zzp": "Freelance",
        "stage": "Internship", "stage/afstudeer": "Internship",
        "internship": "Internship", "parttime": "Part-Time", "part-time": "Part-Time",
        "fulltime": "Full-Time", "full-time": "Full-Time", "contract": "Contract",
    },
    "experience_level": {
        "starter": "Entry level", "starters": "Entry level",
        "geen ervaring": "No experience", "junior": "Junior",
        "medior": "Mid-level", "ervaren": "Experienced", "expert": "Expert",
        "senior": "Senior", "lead": "Lead", "regular": "Regular",
    },
}

# workload_pct y salary_period son numericos/categoricos: no se traducen.
_TEXT_COLUMNS = [
    "title", "description_full", "description_snippet",
    "requirements", "responsibilities", "benefits",
    "company_industry", "salary_raw",
]

# Palabras funcionales inglesas (evidencia fuerte de ingles).
_EN_FUNCTION = {
    "the", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "be", "will", "we", "you", "our", "your", "this", "that", "as", "at",
    "by", "from", "it", "if", "not", "can", "more", "other", "a", "an",
    "have", "has", "we're", "you'll", "we'll", "role", "skills", "benefits",
    "about", "no", "yes", "who", "which", "their", "they", "them", "he",
    "she", "his", "her", "was", "were", "been", "would", "should", "could",
    "may", "might", "must", "shall", "do", "does", "did", "so", "but",
    "than", "then", "there", "where", "when", "how", "all", "any", "each",
    "such", "into", "over", "under", "between", "within", "without",
}

# Terminos de dominio (evidencia debil: aparecen tambien en textos holandeses).
_EN_DOMAIN = {
    "data", "analyst", "analytics", "engineer", "engineering", "manager",
    "developer", "specialist", "consultant", "architect", "scientist",
    "business", "intelligence", "machine", "learning", "product", "owner",
    "senior", "junior", "medior", "lead", "intern", "internship", "frontend",
    "backend", "fullstack", "cloud", "devops", "software", "security",
    "support", "network", "tester", "design", "marketing", "sales",
    "experience", "team", "work", "company", "salary", "job",
    "remote", "hybrid", "on-site", "onsite", "permanent", "temporary",
    "freelance", "part-time", "full-time", "entry", "level", "experienced",
    "expert", "mid-level", "contract", "location", "full", "part", "time",
    "site", "mid",
}

_DUTCH_STRONG = {
    "analist", "analiste", "ontwikkelaar", "beheerder", "medewerker",
    "ingenieur", "adviseur", "functionaris", "stagiair", "ervaren", "gezocht",
    "vacature", "salaris", "maand", "maanden", "uur", "uren", "werkgever",
    "werknemer", "sollicitatie", "arbeidsvoorwaarden", "dienstverband",
    "opleiding", "vaardigheden", "werkzaamheden", "omgeving", "klanten",
    "collega", "verantwoordelijk", "ontwikkeling", "werkplezier", "afdeling",
    "hybride", "thuiswerken", "vacatures", "functie", "fulltime", "parttime",
    "secundair", "secundaire", "pensioen", "vakantiedagen", "vergoeding",
    "geplaatst", "gisteren", "analyseer", "markt", "begrijpen", "zoals",
    "taken", "eisen", "verantwoordelijk", "ontwikkelen", "onderhouden",
    "kennis", "mogelijkheden", "solliciteer", "reageren", "informatie",
    "projecten", "resultaten", "processen", "systemen", "ondersteuning",
    "beheer", "kandidaat", "werkplek", "werktijden", "doel", "groei",
    "samenwerking", "kwaliteit", "veiligheid", "oplossingen", "advies",
    "inzet", "werkervaring", "salarisindicatie", "jaarsalaris",
}

_DUTCH_COMMON = {
    "de", "het", "een", "en", "van", "voor", "met", "op", "bij", "wij", "we",
    "je", "jij", "jouw", "ons", "onze", "niet", "ook", "als", "aan", "naar",
    "om", "dat", "deze", "die", "wordt", "worden", "kan", "kunnen", "moet",
    "moeten", "bieden", "ervaring", "salaris", "functie", "organisatie",
    "binnen", "tot", "meer", "jaar", "jaren", "dagen", "maanden", "klanten",
    "collega", "samen", "werken", "zoeken", "zoekt", "daarnaast", "tijdens",
    "nieuwe", "is", "zijn", "hebben", "heeft", "onze", "team",
}

_GERMAN_STRONG = {
    "und", "oder", "mit", "für", "wir", "sie", "ihre", "unser", "unsere",
    "werden", "wird", "sind", "ist", "nicht", "auch", "bei", "als", "auf",
    "eine", "einen", "einem", "einer", "das", "der", "die", "den", "dem",
    "bewerben", "erfahrung", "kenntnisse", "aufgaben", "wir bieten",
    "stellenangebot", "mitarbeiter", "bereich", "team", "entwicklung",
}

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ']+", re.UNICODE)


def _looks_english(text: str) -> bool:
    if not text:
        return True
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return True
    strong = sum(1 for w in words if w in _DUTCH_STRONG)
    common = sum(1 for w in words if w in _DUTCH_COMMON)
    if len(words) <= 6:
        if strong:
            return False
        en = sum(1 for w in words if w in _EN_FUNCTION or w in _EN_DOMAIN)
        return en >= 1
    # Texto largo: sin ninguna senal holandesa se asume ingles (evita traducir
    # fragmentos ingleses sin palabras funcionales).
    if strong >= 1:
        return False
    if common == 0:
        return True
    en = sum(1 for w in words if w in _EN_FUNCTION)
    return en >= max(2, int(len(words) * 0.02)) and en > common


def _detect_source(text: str) -> str:
    """Devuelve 'de' si parece aleman, si no 'nl'."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    de = sum(1 for w in words if w in _GERMAN_STRONG)
    nl = sum(1 for w in words if w in _DUTCH_STRONG or w in _DUTCH_COMMON)
    return "de" if de > nl and de >= 2 else "nl"


def _chunks(text: str, size: int = _MAX_CHUNK) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    for block in re.split(r"(?<=[.;:!?])\s+|\n", text):
        if not block:
            continue
        if len(block) > size:
            for i in range(0, len(block), size):
                parts.append(block[i:i + size])
        else:
            parts.append(block)
    out: list[str] = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) + 1 > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()]


class Translator:
    def __init__(self, cache_path: str | Path | None = None,
                 backend: str = "argos", target: str = "en", timeout: int = 30,
                 request_rng: tuple[float, float] = (0.0, 0.0)):
        self.backend = backend
        self.target = target
        self.timeout = timeout
        self.request_rng = request_rng
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, str] = {}
        self._argos_ready = False
        self._argos_pairs: set[tuple[str, str]] = set()
        self._session = None
        self._load_cache()
        self.stats = {"cells": 0, "translated": 0, "cached": 0, "skipped": 0,
                      "failed": 0}

    # ------------------------------------------------------------ cache
    def _load_cache(self) -> None:
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
                log.info("Cache de traduccion: %d entradas", len(self._cache))
            except (OSError, ValueError):
                self._cache = {}

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("No se pudo guardar la cache: %s", e)

    # ------------------------------------------------------- traduccion
    def translate_text(self, text: str) -> str:
        if not text or not text.strip():
            return text
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if key in self._cache:
            self.stats["cached"] += 1
            return self._cache[key]
        if _looks_english(text):
            self.stats["skipped"] += 1
            return text
        try:
            translated = self._backend_translate(text)
        except Exception as e:  # noqa: BLE001
            log.warning("Traduccion fallida (%s); se conserva el original", e)
            self.stats["failed"] += 1
            return text
        if translated and translated != text:
            self._cache[key] = translated
            self.stats["translated"] += 1
            return translated
        return text

    def _backend_translate(self, text: str) -> str:
        if self.backend == "argos":
            return self._argos(text)
        return self._google(text)

    # ------------------------------------------------------------ argos
    def _ensure_argos(self) -> None:
        if self._argos_ready:
            return
        import argostranslate.package as pkg
        from argostranslate import translate as argos_translate
        # Instala pares si faltan (nl->en, de->en).
        try:
            pkg.update_package_index()
            available = pkg.get_available_packages()
        except Exception:
            available = []
        installed = argos_translate.get_installed_languages()
        codes = {lang.code for lang in installed}
        for src in ("nl", "de"):
            if (src, self.target) not in self._argos_pairs:
                if src in codes and self.target in codes:
                    self._argos_pairs.add((src, self.target))
                    continue
                cand = [x for x in available
                        if x.from_code == src and x.to_code == self.target]
                if cand:
                    try:
                        pkg.install_from_path(cand[0].download())
                        self._argos_pairs.add((src, self.target))
                    except Exception as e:  # noqa: BLE001
                        log.warning("No se pudo instalar modelo %s->%s: %s",
                                    src, self.target, e)
        self._argos_ready = True

    def _argos(self, text: str) -> str:
        from argostranslate import translate as argos_translate
        self._ensure_argos()
        src = _detect_source(text)
        if (src, self.target) not in self._argos_pairs:
            src = "nl"
        out = []
        for chunk in _chunks(text, size=2000):
            out.append(argos_translate.translate(chunk, src, self.target))
        return " ".join(out).strip()

    # ----------------------------------------------------------- google
    def _google(self, text: str) -> str:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36",
                "Accept": "application/json", "Accept-Encoding": "gzip, deflate"})
        out = []
        for chunk in _chunks(text):
            data = {"client": "gtx", "sl": "auto", "tl": self.target,
                    "dt": "t", "q": chunk}
            resp = self._session.post(_ENDPOINT, data=data, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            segs = payload[0] if payload and isinstance(payload, list) else []
            out.append("".join(s[0] for s in segs if s and s[0]))
            self._sleep()
        return "".join(out).strip()

    def _sleep(self) -> None:
        import random
        if self.request_rng and self.request_rng[0] > 0:
            time.sleep(random.uniform(*self.request_rng))

    # ------------------------------------------------------- dataframe
    def translate_dataframe(self, df: pd.DataFrame,
                            columns: Optional[list[str]] = None,
                            limit: int = 0) -> pd.DataFrame:
        cols = columns or (_TEXT_COLUMNS + list(_CATEGORICAL.keys()))
        cols = [c for c in cols if c in df.columns]
        log.info("Traduciendo columnas: %s (backend=%s)", cols, self.backend)

        for col, mapping in _CATEGORICAL.items():
            if col not in df.columns:
                continue
            def _map(v, m=mapping):
                norm = str(v).strip().lower()
                return m.get(norm, v) if norm else v
            df[col] = df[col].map(_map)

        processed = 0
        stop = False
        for col in cols:
            if stop:
                break
            series = df[col]
            new_values = list(series)
            for i, v in enumerate(series):
                if not isinstance(v, str) or not v.strip():
                    continue
                self.stats["cells"] += 1
                new_values[i] = self.translate_text(v)
                processed += 1
                if processed % 100 == 0:
                    self.save_cache()
                    log.info("  ... %d celdas (traducidas %d, cacheadas %d, "
                             "ingles %d, fallos %d)", processed,
                             self.stats["translated"], self.stats["cached"],
                             self.stats["skipped"], self.stats["failed"])
                if limit and processed >= limit:
                    stop = True
                    break
            df[col] = new_values
        self.save_cache()
        log.info("Traduccion: celdas=%d traducidas=%d cacheadas=%d ingles=%d fallos=%d",
                 self.stats["cells"], self.stats["translated"], self.stats["cached"],
                 self.stats["skipped"], self.stats["failed"])
        return df


def translate_dataframe(df: pd.DataFrame, cache_path: str | Path | None = None,
                        limit: int = 0) -> pd.DataFrame:
    t = Translator(cache_path=cache_path)
    return t.translate_dataframe(df, limit=limit)
