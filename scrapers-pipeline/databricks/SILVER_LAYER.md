# Silver Layer - Guía de Implementación

## Resumen de las 4 tablas Bronze

| Scraper | Campos | Clave primaria | País focus |
|---------|--------|----------------|------------|
| **Indeed** | 32 | `job_key` | ES |
| **LinkedIn** | 27 | `job_id` | Multi |
| **InfoJobs** | 18 | `id_oferta` | ES |
| **Multi-site** (IrishJobs, StepStone NL, jobs.ch, Glassdoor) | 37 | `job_id` | IE, NL, CH |

---

## Problemas de calidad detectados en los datos brutos

1. **Contaminación cruzada**: El CSV de `stepstone_nl` tiene URLs de `irishjobs.ie` y datos de ubicación de Irlanda mezclados
2. **Salarios**: Muchos nulos, formatos inconsistentes (`"€100,483 per annum"` como texto libre vs `salary_min`/`salary_max` numéricos)
3. **Fechas**: Nombres y formatos distintos (`posted_date` vs `posted_datetime` vs `fecha_publicacion`)
4. **Ubicaciones**: Nombres inconsistentes (`city` vs `location_city`, `state` vs `location_region`)
5. **Booleans**: Diferentes convenciones (`is_remote` vs `work_mode` como string, `is_sponsored` vs `salary_disclosed`)
6. **HTML**: `description_html` con tags HTML crudos, `description_snippet` con JavaScript basura
7. **Skills**: Solo LinkedIn tiene `skills` como lista estructurada; los demás no
8. **Trazabilidad**: Nombres distintos (`search_term` vs `search_role`, `city_query` vs `search_city`)

---

## Pasos para la capa Silver

### Paso 1: Definir el esquema unificado

Crea un DataFrame vacío con el schema target común:

```
job_id, job_url, title, company_name, location_city, location_region, location_country,
posted_date, work_mode, employment_type, experience_level, salary_min, salary_max,
salary_currency, salary_period, description_clean, skills, source_scraper, search_role,
search_city, scraped_at, _ingest_date
```

### Paso 2: Normalización de nombres de columnas

Para cada tabla bronze, crea un **mapping de columnas** (rename). Ejemplo para Indeed:

| Bronze (Indeed) | Silver (unificado) |
|-----------------|-------------------|
| `job_key` | `job_id` |
| `company` | `company_name` |
| `city` | `location_city` |
| `state` | `location_region` |
| `country` | `location_country` |
| `description_text` | `description_clean` |
| `search_term` | `search_role` |
| `city_query` | `search_city` |

Para LinkedIn:

| Bronze (LinkedIn) | Silver (unificado) |
|-------------------|-------------------|
| `job_id` | `job_id` |
| `company_name` | `company_name` |
| `location_city` | `location_city` |
| `location_region` | `location_region` |
| `location_country` | `location_country` |
| `work_mode` | `work_mode` |
| `employment_type` | `employment_type` |
| `search_role` | `search_role` |
| `search_city` | `search_city` |

Para InfoJobs:

| Bronze (InfoJobs) | Silver (unificado) |
|-------------------|-------------------|
| `id_oferta` | `job_id` |
| `empresa` | `company_name` |
| `ciudad` | `location_city` |
| `provincia` | `location_region` |
| `pais` | `location_country` |
| `fecha_publicacion` | `posted_date` |
| `modalidad` | `work_mode` |
| `jornada` | `employment_type` |
| `experiencia_min` | `experience_level` |
| `salario_min` | `salary_min` |
| `salario_max` | `salary_max` |
| `moneda` | `salary_currency` |
| `periodo` | `salary_period` |
| `keyword_buscada` | `search_role` |
| `ciudad_buscada` | `search_city` |

Para Multi-site:

| Bronze (Multi-site) | Silver (unificado) |
|---------------------|-------------------|
| `job_id` | `job_id` |
| `company_name` | `company_name` |
| `location_city` | `location_city` |
| `location_region` | `location_region` |
| `location_country` | `location_country` |
| `work_mode` | `work_mode` |
| `employment_type` | `employment_type` |
| `experience_level` | `experience_level` |
| `search_role` | `search_role` |
| `search_city` | `search_city` |

### Paso 3: Tipos de datos estandarizados

- **Fechas**: convertir strings a `TimestampType`
- **Salarios**: asegurar `IntegerType` / `DoubleType`
- **Booleans**: normalizar a `BooleanType`
- **Strings vacíos** (`""`) → `null`

### Paso 4: Limpieza de texto

- `description_html`: extraer texto plano con UDF o `regexp_replace`
- `description_snippet`: limpiar JavaScript basura
- Normalizar espacios múltiples y saltos de línea

### Paso 5: Normalización de valores categóricos

- **`work_mode`**: mapear a `["Remote", "Hybrid", "Onsite", null]`
  - Indeed: `is_remote` (bool) + `workplace_type` (string) → combinar
  - LinkedIn: `work_mode` ya viene normalizado
  - InfoJobs: `modalidad` → mapear
  - Multi-site: `work_mode` ya viene normalizado

- **`employment_type`**: estandarizar
  - `"Jornada completa"` → `"Full-time"`
  - `"Media jornada"` → `"Part-time"`
  - `"Contrato"` → `"Contract"`
  - `"Prácticas"` → `"Internship"`

- **`experience_level`**: usar el mapping de `EXPERIENCE_LEVEL_MAP` (definido en linkedin/models.py):
  - `"internship"`, `"prácticas"`, `"becario"` → `"Practicas"`
  - `"entry level"`, `"entry"` → `"Entry"`
  - `"associate"` → `"Associate"`
  - `"mid-senior level"`, `"senior"` → `"Mid-Senior"`
  - `"director"` → `"Director"`
  - `"executive"` → `"Executive"`

- **`salary_period`**: estandarizar a `["year", "month", "hour"]`

### Paso 6: Deduplicación

- **Intra-fuente**: por `job_id`, conservar el más reciente o el más completo
- **Cross-source**: si el mismo puesto aparece en Indeed y LinkedIn (mismo título + company + city), crear un `job_id_unified` o marcarlo con flag

### Paso 7: Enriquecimiento (opcional)

- Extraer `skills` desde `description_text` usando keyword matching (ver `DATA_SKILLS_KEYWORDS` en linkedin/models.py)
- Parsear salario cuando solo existe `salary_raw` pero no `salary_min`/`salary_max`
- Inferir `work_mode` desde el texto de la descripción si está vacío

### Paso 8: Escritura

Escribe cada tabla silver como Delta en:
- `job_offers.silver.indeed`
- `job_offers.silver.linkedin`
- `job_offers.silver.infojobs`
- `job_offers.silver.multi_site`

Opcionalmente, tabla unificada: `job_offers.silver.jobs`

---

## Orden sugerido de implementación

1. Empieza con **una sola tabla** (Indeed es la más completa, 32 campos) para practicar
2. Haz el rename + cast de tipos → escribe a silver
3. Repite el patrón para LinkedIn, InfoJobs, Multi-site
4. Finalmente crea la tabla unificada si quieres practicar `UNION ALL`

---

## Referencia: Campos por scraper

### Indeed (32 campos)

```
job_key, viewjob_url, apply_url, title, company, company_id_encrypted,
company_rating, company_review_count, company_overview_link, location, city,
state, country, salary_min, salary_max, salary_type, salary_text, is_remote,
job_type, snippet, description_html, description_text, posted_relative,
posted_date, benefits, contract_type, schedule, workplace_type, is_sponsored,
city_query, search_term, page, scraped_at
```

### LinkedIn (27 campos)

```
job_id, job_url, title, company_name, location_raw, location_city,
location_region, location_country, posted_datetime, posted_relative, is_new,
company_url, company_industry, company_size, work_mode, employment_type,
experience_level, salary_raw, salary_min, salary_max, salary_currency,
salary_period, num_applicants, description_full, role_summary,
company_description, responsibilities, requirements, benefits, skills,
search_role, search_city, source, scraped_at
```

### InfoJobs (18 campos)

```
id_oferta, titulo, empresa, ciudad, provincia, pais, fecha_publicacion,
categoria, salario_raw, salario_min, salario_max, moneda, periodo, jornada,
tipo_contrato, experiencia_min, modalidad, descripcion_snippet, url_oferta,
fecha_scraped, fuente, ciudad_buscada, keyword_buscada, pagina
```

### Multi-site (37 campos)

```
job_id, job_url, title, company_name, location_raw, location_city,
location_region, location_country, posted_datetime, posted_relative, is_new,
company_url, company_industry, company_size, work_mode, employment_type,
experience_level, salary_raw, salary_min, salary_max, salary_currency,
salary_period, num_applicants, description_full, description_snippet,
role_summary, company_description, responsibilities, requirements, benefits,
skills, site, country, workload_pct, salary_disclosed, search_role,
search_city, source, scraped_at
```
