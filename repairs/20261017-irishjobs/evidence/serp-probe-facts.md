# Sondas de navegador real sobre irishjobs.ie — hechos observados
# Fecha: 2026-10-17 (~07:34–07:47 UTC). Sonda: Chrome propio de agent-browser
# (UA nativo Chrome/154, Windows; locale es-ES), un solo navegador, pocas
# peticiones. Sin sesión de la persona y sin credenciales.

## 1. SERP nacional (la del run): /jobs/python-data?age=7

- Título: "Python Data job vacancies - Updated Daily".
- h1: "188 Python Data jobs".
- Conteos en el DOM renderizado (evaluados en la página):
  - `document.querySelectorAll('[data-testid="job-item"]').length` = 25
  - `[data-testid="job-card-content"]` = 25
  - `[data-testid="job-item-title"]` = 25
  - Atributos `data-at` presentes en las tarjetas (los que usa src/irishjobs/serp.py):
    - `job-item-company-name` = 25  (SEL_ST_COMPANY)
    - `job-item-location` = 25      (SEL_ST_LOCATION)
    - `job-item-timeago` = 25       (SEL_ST_TIMEAGO)
    - `job-item-salary-info` = 25
    - `jobcard-content` = 25 (variante de `job-card-content`)
    - `search-jobs-count` = 1 (contador de resultados de la búsqueda)
  - Banner de consentimiento ("We use cookies", botones "Just Necessary" /
    "Accept All" / "Cookie Settings") visible en el DOM principal; las tarjetas
    están en el DOM aunque el banner no se haya descartado.
- Ejemplo de tarjeta (texto): "Reperio Human Capital Ltd / Staff Data Engineer /
  Dublin, County Dublin / €144,000 - €156,000 per annum / ... / 2 weeks ago".
- Enlace de detalle: https://www.irishjobs.ie/job/staff-data-engineer/reperio-human-capital-ltd-job108057919
  -> el patrón `-job<id>` del regex de serp.py (`_RE_JOB_ID_FROM_URL`) encaja.

## 2. SERP de ciudad del run: /jobs/bi-developer/in-dundalk?age=7

- Título: "BI Developer Jobs in Dundalk - Updated Daily | ...".
- h1: "0 BI Developer jobs in Dundalk  + 30 km".
- `[data-testid="job-item"]` = 1 (tarjeta sugerida: "Senior PowerBI Analyst -
  Contract Dublin 6-18 Months", Adecco, Dublin).
- Conclusión: la SERP real puede traer 0 resultados propios y aun así 1 tarjeta
  sugerida; el scraper no debe confundir "0 resultados" con bloqueo (ver §4).

## 3. Estado de "sin resultados" (control): /jobs/zzzqqq-nonexistent-role-xyz?age=7

- `[data-at="unified-resultlist"]` presente (contenedor del listado).
- `[data-testid="job-item"]` = 0.
- Texto del body: "There is no job matching your search Zzzqqq Nonexistent Role
  Xyz".
- Conclusión: un SERP vacío legítimo conserva el contenedor del listado y el
  mensaje "There is no job matching your search".

## 4. Paginación

- No existe `link[rel="next"]` en la página.
- La navegación está en `nav[aria-label="pagination"]`; los `href` reales son
  `.../jobs/python-data?page=N&ag=7` (page como primer parámetro).
- El scraper construye `?page=2&action=paging_next&age=7` (serp.build_search_url),
  que cae en `Disallow: /*&action=` y `/*?action=` de robots.txt (ver
  robots-irishjobs.txt). El enlace propio del sitio NO contiene `action`.

## 5. Detalle de oferta (muestra)

- URL: https://www.irishjobs.ie/job/staff-data-engineer/reperio-human-capital-ltd-job108057919
- JSON-LD `JobPosting` presente en la página:
  - title: "Staff Data Engineer"
  - description: 1726 caracteres (texto completo)
  - datePosted: "2026-09-29T13:08:04.08Z" (fecha exacta)
  - employmentType: ["CONTRACTOR", "REMOTE_WORKING"]
  - baseSalary: null (esta oferta no publica salario)
  - industry: "IT, IT-Database"
  - hiringOrganization.name: "Reperio Human Capital Ltd"
- `data-testid` del detalle: `harmonised-apply-button`, sin contenedor de
  descripción con testid; el parser actual usa JSON-LD primero (detail.py).
- `employmentType` con `REMOTE_WORKING` es señal estructurada de work_mode que
  el scraper hoy NO mapea (detalle.py solo copia employment_type).

## 6. Señales anti-bot observadas (navegador real)

- Cookie `ak_bmsc` en el navegador de la sonda (Akamai Bot Manager).
- Script Akamai cargado por la página: `/akam/13/4461d845`; pixel
  `/akam/13/pixel_4461d845`.
- POST del sensor Akamai a ruta ofuscada (201):
  `/jXY_/vCxv/pKOl/4Oar/0Q/Vim7zkVLQtzwSb/Bz5ACz8D/KzU7L/QVIQ3I`
  (aparece 2 veces; también un GET 200 a la primera variante).
- Cabeceras `ak_p` (server-timing) y `x-akamai-transformed` en el documento.
- Consola: avisos de un widget Turnstile sin widget asociado ("Cannot find
  Widget cf-chl-widget-...") y errores de hidratación React #418/#423 de
  `unifiedResultlist.js`; ningún CAPTCHA/challenge visible en las 5 vistas.
- No se resolvió ningún challenge ni se usaron servicios de pago.
