# Evidencia de cierre — spec 004 (reparación de scrapers caídos)

- **Alcance:** cierre de la spec 004. Comprobación real de la reparación de
  InfoJobs (probada y con push validado), suite del núcleo en verde y listado de
  fuentes no reparadas aún con su playbook y su evidencia de partida.
- **Fecha:** 2026-10-11.
- **Rama de la pila:** la suite y esta evidencia se ejecutan en
  `repair/linkedin-20261011` (cima de la pila de reparaciones, descendiente de
  la rama base `spec/004-repair-failed-scrapers`). Las tres ramas de reparación
  (`repair/infojobs-20261005`, `repair/indeed-20261010`,
  `repair/linkedin-20261011`) están en `origin` con el push validado por la
  persona (RF-12).

## 1. Suite del núcleo

- **Comando:** `python -m pytest scrapers-pipeline/tests -q` (desde la raíz del
  repositorio).
- **Resultado (estado final, tras actualizar los registros):**
  `1126 passed in 543.53s (0:09:03)`. Primera ejecución previa a esos cambios:
  `1126 passed in 446.31s (0:07:26)`.
- **Fecha:** 2026-10-11.
- **Observaciones:** sin fallos ni warnings relevantes; incluye la integración
  offline del núcleo de reparaciones (`targets → brief → registro → umbral →
  calidad`) y la auditoría de higiene de `repairs/` (T-15).

## 2. Comprobación real de cierre (InfoJobs)

- **Fuente:** `infojobs` (playbook §6.1).
- **Rama del fix:** `repair/infojobs-20261005` (validada y en `origin`).
- **Prueba en vivo acotada:** 1 keyword × 1 ciudad × 1 página
  (madrid / comercial / p.1), modo CDP sobre un Chrome lanzado directamente con
  la copia de la sesión real; sin subida a Azure, sin landing, sin merge y sin
  solvers.
- **Recuento verificado:** `RESULT total=4 incidencias=0 blocked=false` (exit 0);
  5 tarjetas parseadas, 4 únicas. Umbral exigido: 1 → **cumplido**.
- **Calidad:** veredicto de `quality.py` **OK**; obligatorios (`title`,
  `company`, `description`) al 100 % en la muestra, sin regresión.
- **Registro:** `repairs/20261005-infojobs/` (estado `probado`, con
  `quality_after.json` y la evidencia de la prueba en vivo).
- **Validación del push (RF-12):** validada por la persona; la rama de fix está en `origin`.
- **Historial:** `repairs/history.json` → `infojobs` `best_verified_offers: 4`
  (umbral futuro: 5).

## 3. Reparaciones ya probadas en la pila

| Fuente | Tarea | Rama | Recuento verificado | Calidad | Estado | Merge |
|---|---|---|---|---|---|---|
| indeed | T-32 | `repair/indeed-20261010` | 11 ofertas (umbral 1); descripción 11/11 (1 embebida + 10 de panel) | OK: obligatorios al 100 %, sin regresión; `salary` 90,9 % y `work_mode` 54,5 % (sin meta; comparados a mano con el export 2026-10-06: 61 % y 37 %) | probado | pendiente |
| linkedin | T-33 | `repair/linkedin-20261011` | 34 ofertas en la muestra (umbral 1; 1 rol × 1 ciudad × 50 tarjetas, modo guest) | OK: `description` 90,2 → 100 %, `work_mode` 42,6 → 61,8 %, `skills` 82,1 → 97,1 %, `salary` 17,8 → 14,7 % (dentro de la tolerancia de 5 pp); histórico de 954 filas sin descripción sin backfill, justificado por `robots.txt`/TOS | probado | pendiente |

- **Pendientes de merge:** ambas ramas están en `origin` con el push validado
  por la persona; el merge a `main` es el checkpoint de la persona.
- **Historial:** `repairs/history.json` → `indeed` 11 y `linkedin` 34 (umbrales
  futuros: 12 y 35).

## 4. Fuentes no reparadas aún

Objetivos del diagnóstico refrescado (`python -m repair.cli targets` desde
`scrapers-pipeline/`, run de origen 2026-10-11): **14 objetivos (4 primarios,
10 secundarios)**.

**Primarias fallidas del run 2026-10-11:** `irishjobs` (§6.4), `nvb` (§6.6),
`jobs_ch` (§6.6) y `glassdoor` (§6.5).

**Secundarias del run 2026-10-11:** `indeed` (`skills` 0 %, `work_mode`
54,5 %; §6.2), `linkedin` (`description` 90,2 %, `salary` 17,8 %, `work_mode`
42,6 %; §6.3), `infojobs` (`salary` 59,4 %, `skills` 0 %; §6.1) y
`stepstone_nl` (`salary`/`skills`/`work_mode` 0 %; §6.6). `indeed`, `linkedin`
e `infojobs` ya tienen su reparación original `probado`: estos secundarios son
investigaciones nuevas del último diagnóstico (mejoras opcionales), no
reaperturas de la spec.

Tabla de fuentes pendientes con su playbook y su evidencia de partida:

| Fuente | Playbook | Evidencia de partida |
|---|---|---|
| irishjobs | §6.4 IrishJobs — 0 tarjetas y watchdog 75 | `multi_site_job_scraper/data/merged/last_run.json` (`watchdog_blocked`, `watchdog_no_progress`) y `multi_site_job_scraper/data/irishjobs/run.log`; run 2026-10-03: 0 tarjetas en todos los combos, ningún `PROGRESS` y `exit=75`; run 2026-10-11: `failed`/`error` |
| glassdoor | §6.5 Glassdoor — watchdog por descripciones | `multi_site_job_scraper/data/merged/last_run.json` (`watchdog_blocked`, `watchdog_no_progress`) y `multi_site_job_scraper/data/glassdoor/run.log`; run 2026-10-03: el BFF devuelve 30 ofertas y el bucle de descripciones (hasta 30 × 60 s) no emite `PROGRESS`, `exit=75`; run 2026-10-11: `failed`/`error` |
| stepstone_nl | §6.6 StepStone NL — completitud de campos opcionales | Run 2026-10-03: 3 ofertas con `salary`/`skills`/`work_mode` al 0 % (§2.2); vía de mejora: detalle JSON-LD/DOM (§6.6); artefactos multi-site de §2.3 |
| nvb | §6.6 NVB — completitud de campos opcionales | Run 2026-10-03: `work_mode` 0 % (55 inválidos) sobre API con `workingPlace`/`contractType` (§2.2, §6.6); en el run 2026-10-11 pasa a primaria fallida (`failed`/`error`) |
| jobs_ch | §6.6 Jobs.ch — completitud de campos opcionales | Run 2026-10-03: `salary` 40,4 %, `skills` 32,7 %, `work_mode` 11,5 %; vía de mejora: detalle JSON-LD y cabeceras (§6.6); en el run 2026-10-11 pasa a primaria fallida (`failed`/`empty`) |

- **Nota de vigencia:** el plan §2.2 (run 2026-10-03) no es una lista cerrada;
  el proceso recalcula los objetivos con el último diagnóstico (plan §2), por lo
  que el listado anterior es el vigente a esta fecha.
- La decisión sobre las secundarias (grupo 9) y sobre las rutas de recuperación
  del histórico de LinkedIn es de la persona.

## 5. Pendientes de cierre de la spec

- **Merge de la pila a `main`:** checkpoint de la persona (merge `--no-ff` de
  las ramas de reparación y de la rama de la spec); nada se ha fusionado aún.
- **Cierre y tag:** `git tag -a spec-004 …` y borrado de la rama, pendientes de
  ese merge; esta evidencia y la suite en verde son la comprobación real.
- **Grupo 8 restante (IrishJobs, Glassdoor) y grupo 9 (`stepstone_nl`, `nvb`,
  `jobs_ch`):** se ejecutarán con el proceso aún sin fusionar en `main`, por
  decisión de la persona; cada uno en su rama `repair/<fuente>-<fecha>`, con su
  registro, sus puertas (umbral + calidad) y la validación del push.
- **T-40:** queda cerrada por esta evidencia junto con la suite del núcleo en
  verde.
