"""Modelo de datos de una oferta de empleo de Indeed.

JobOffer es un dataclass con los campos que el scraper extrae de cada job card
de la SERP. Se llena combinando:
  - el array plano de window._initialData (metadatos: empresa, ubicacion, salario, fecha)
  - el array GraphQL de window._initialData (descripcion HTML y URL externa de aplicacion)
  - fallback a selectores HTML cuando el JSON no trae un campo

El prototipo devuelve SOLO datos del listado (no entra a /viewjob) pero ya
incluye la descripcion completa y la URL de aplicacion porque Indeed las
incluye embebidas en el JSON de la propia SERP (array GraphQL, job.description.html).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class JobOffer:
    # Identificacion
    job_key: str = ""                        # jk de Indeed (hex 16), clave de deduplicacion
    viewjob_url: str = ""                    # https://<domain>/viewjob?jk=<jk>
    apply_url: str = ""                      # URL externa de aplicacion (job.url en GraphQL)

    # Contenido principal
    title: str = ""
    company: str = ""
    company_id_encrypted: str = ""           # fccid / companyIdEncrypted
    company_rating: float | None = None
    company_review_count: int | None = None
    company_overview_link: str = ""

    # Ubicacion
    location: str = ""                       # formattedLocation (texto completo)
    city: str = ""                           # jobLocationCity
    state: str = ""                          # jobLocationState
    is_remote: bool = False                  # heuristica: "remoto" en location/title

    # Salario (solo ~3 de 16 ofertas lo traen)
    salary_min: int | None = None            # extractedSalary.min
    salary_max: int | None = None            # extractedSalary.max
    salary_type: str = ""                    # extractedSalary.type (YEARLY/HOURLY/MONTHLY...)
    salary_text: str = ""                    # texto literal del snippet de salario

    # Tipo y condiciones
    job_type: str = ""                       # "Jornada completa", etc. (attribute_snippet)

    # Descripcion
    snippet: str = ""                        # belowJobSnippet (bullets cortos)
    description_html: str = ""               # job.description.html (descripcion completa, GraphQL)
    description_text: str = ""               # texto plano de description_html (sin tags HTML)
    posted_relative: str = ""                # formattedRelativeTime ("hace 30+ dias")
    posted_date: str = ""                    # ISO date derivado de datePublished/createDate

    # Condiciones (extraidas del panel derecho via enrich)
    benefits: str = ""                       # beneficios listados
    contract_type: str = ""                  # Jornada completa / parcial / indefinido / temporal
    schedule: str = ""                       # Horario, turnos
    workplace_type: str = ""                 # remote / hybrid / onsite (derivado de remoteWorkModel)

    # Flags
    is_sponsored: bool = False

    # Trazabilidad de la ejecucion
    country: str = ""
    city_query: str = ""
    search_term: str = ""
    page: int = 0
    scraped_at: str = ""                     # ISO timestamp UTC

    def fill_trace(self, country: str, city_query: str, search_term: str, page: int) -> None:
        self.country = country
        self.city_query = city_query
        self.search_term = search_term
        self.page = page
        self.scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def fill_posted_date_typed(self) -> None:
        """Convierte posted_date de ISO string a datetime UTC. Se llama en
        runner tras dedup, antes de export, para que los exportadores reciban
        datetime nativos (Parquet/Delta conservan el tipo)."""
        if self.posted_date:
            try:
                self._posted_dt = datetime.fromisoformat(self.posted_date)
            except (ValueError, TypeError):
                self._posted_dt = None
        else:
            self._posted_dt = None
        if self.scraped_at:
            try:
                self._scraped_dt = datetime.fromisoformat(self.scraped_at)
            except (ValueError, TypeError):
                self._scraped_dt = None
        else:
            self._scraped_dt = None

    def to_typed_dict(self) -> dict[str, Any]:
        """Como to_dict pero con datetime nativos para posted_date/scraped_at.
        Usado por schema.parse_typed_df() para construir el DataFrame tipado."""
        d = asdict(self)
        d["posted_date"] = getattr(self, "_posted_dt", None)
        d["scraped_at"] = getattr(self, "_scraped_dt", None)
        return d

    def finalize_urls(self, domain: str) -> None:
        if self.job_key and not self.viewjob_url:
            self.viewjob_url = f"https://{domain}/viewjob?jk={self.job_key}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def fieldnames(cls) -> list[str]:
        return list(cls.__dataclass_fields__.keys())
