from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator


class Offer(BaseModel):
    id_oferta: str
    titulo: str
    empresa: str | None = None
    ciudad: str
    provincia: str
    pais: str = "España"
    fecha_publicacion: date | None = None
    categoria: str | None = None
    salario_raw: str | None = None
    salario_min: int | None = None
    salario_max: int | None = None
    moneda: str | None = None
    periodo: str | None = None
    jornada: str | None = None
    tipo_contrato: str | None = None
    experiencia_min: str | None = None
    modalidad: str | None = None
    descripcion_snippet: str | None = None
    url_oferta: str
    fecha_scraped: datetime = Field(default_factory=datetime.now)
    fuente: str = "infojobs"
    ciudad_buscada: str
    keyword_buscada: str
    pagina: int

    @field_validator("fecha_scraped", mode="before")
    @classmethod
    def coerce_fecha_scraped(cls, v: object) -> datetime:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        if isinstance(v, datetime):
            return v
        return datetime.now()
