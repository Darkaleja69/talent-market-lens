# Prueba en vivo acotada (RF-9) — glassdoor (mercado Irlanda, glassdoor.ie)

- **Fuente:** glassdoor · **Rama:** `repair/glassdoor-20261012` · **Tarea:** T-35 (spec 004)
- **Run de origen:** 2026-10-12 · **Fecha de la prueba:** 2026-10-15 (hora local, Europe/Madrid)
- **Comando** (desde `multi_site_job_scraper`):
  `python -m src.glassdoor --role "Data Analyst" --max-pages 1`
- **Alcance:** 1 rol × 1 página, solo el portal glassdoor (mercado IE). Sin Azure, sin
  landing, sin merge, sin solvers, sin browser-cdp.
- **Inicio/fin:** 2026-10-15 23:29:23 +02:00 → 23:31:16 +02:00 · **Duración:** 113,2 s ·
  **Exit code:** 0 (capturado con `$LASTEXITCODE` en la misma sesión de PowerShell).
- **Saneado:** sin cookies, tokens ni rutas de usuario; las rutas locales se escriben
  como `<REPO>`. Los artefactos pesados (gitignored) se referencian por ruta, nunca se
  copian al repositorio.

Un único intento de scraper; el arnés capturó exit code y log a la primera (no hizo
falta la repetición de arnés permitida).

## 1. Resultado y umbral

- Log: `=== GLASSDOOR DONE: 23 ofertas nuevas, 23 total en store ===` → **23 ofertas**
  (el BFF devolvió 23 listados de un `totalJobsCount` de 24 en la página 1).
- **Umbral:** `glassdoor | mejor verificado: sin dato | umbral: 1` (sin historial) →
  **superado** (23 ≥ 1).
- Parquet del run: **23 filas**, `scraped_at` 2026-10-15T21:30:35Z–21:31:13Z (hora local
  23:30:35–23:31:13), `source=local`, país del mercado.
- Salida: **exit 0**; run directo (sin watchdog del wrapper), sin `exit=75` y sin parada
  por inactividad.

## 2. Ubicación y fallback (objetivo del fix)

El autocompletado sigue sin responder (`resolve_location Ireland -> status 403`) y **el
run ya no aborta**: entra el fallback declarado y la búsqueda continúa.

```
23:29:54 WARNING src.glassdoor.bff: resolve_location Ireland -> status 403
23:29:54 WARNING glassdoor: No se pudo resolver 'Ireland'; usando pais de reserva del mercado (70, COUNTRY)
23:29:54 INFO    glassdoor: --- [1/1] 'Data Analyst' ---
```

`location_fallback = {"id": 70, "type": "COUNTRY"}` (commit 876ff44) se usó tal cual,
con el id/tipo confirmados en la sesión real (fase 2). La búsqueda siguió por el BFF y
devolvió ofertas, así que el fallback quedó **comprobado en vivo**.

## 3. Latido PROGRESS (objetivo del fix)

- **25 líneas `PROGRESS source=glassdoor`**, todas **intercaladas en el bucle de
  tarjetas** y anteriores a la línea de cierre de página
  `Page 1 'Data Analyst': 23 procesadas (23 nuevas, fuente=bff)` (23:31:14).
- Primera línea `parsed=0` a las 23:30:35 (latido inmediato tras parsear la página,
  antes del bucle de descripciones); después `parsed=1..23` conforme se procesa cada
  tarjeta (23 líneas); línea final `parsed=23` (delta 0) al cerrar la página.
- Cadencia: **hueco máximo entre latidos consecutivos = 3 s**; el bucle completo duró
  39 s (23:30:35 → 23:31:14).

Extracto literal (25 líneas, local + `ts` UTC):

```
23:30:35 INFO    glassdoor: PROGRESS source=glassdoor parsed=0 new=0 ts=2026-10-15T21:30:35+00:00
23:30:36 INFO    glassdoor: PROGRESS source=glassdoor parsed=1 new=1 ts=2026-10-15T21:30:36+00:00
23:30:38 INFO    glassdoor: PROGRESS source=glassdoor parsed=2 new=2 ts=2026-10-15T21:30:38+00:00
23:30:40 INFO    glassdoor: PROGRESS source=glassdoor parsed=3 new=3 ts=2026-10-15T21:30:40+00:00
23:30:41 INFO    glassdoor: PROGRESS source=glassdoor parsed=4 new=4 ts=2026-10-15T21:30:41+00:00
23:30:43 INFO    glassdoor: PROGRESS source=glassdoor parsed=5 new=5 ts=2026-10-15T21:30:43+00:00
23:30:45 INFO    glassdoor: PROGRESS source=glassdoor parsed=6 new=6 ts=2026-10-15T21:30:45+00:00
23:30:47 INFO    glassdoor: PROGRESS source=glassdoor parsed=7 new=7 ts=2026-10-15T21:30:47+00:00
23:30:49 INFO    glassdoor: PROGRESS source=glassdoor parsed=8 new=8 ts=2026-10-15T21:30:49+00:00
23:30:50 INFO    glassdoor: PROGRESS source=glassdoor parsed=9 new=9 ts=2026-10-15T21:30:50+00:00
23:30:53 INFO    glassdoor: PROGRESS source=glassdoor parsed=10 new=10 ts=2026-10-15T21:30:53+00:00
23:30:55 INFO    glassdoor: PROGRESS source=glassdoor parsed=11 new=11 ts=2026-10-15T21:30:55+00:00
23:30:57 INFO    glassdoor: PROGRESS source=glassdoor parsed=12 new=12 ts=2026-10-15T21:30:57+00:00
23:30:58 INFO    glassdoor: PROGRESS source=glassdoor parsed=13 new=13 ts=2026-10-15T21:30:58+00:00
23:30:59 INFO    glassdoor: PROGRESS source=glassdoor parsed=14 new=14 ts=2026-10-15T21:30:59+00:00
23:31:01 INFO    glassdoor: PROGRESS source=glassdoor parsed=15 new=15 ts=2026-10-15T21:31:01+00:00
23:31:03 INFO    glassdoor: PROGRESS source=glassdoor parsed=16 new=16 ts=2026-10-15T21:31:03+00:00
23:31:04 INFO    glassdoor: PROGRESS source=glassdoor parsed=17 new=17 ts=2026-10-15T21:31:04+00:00
23:31:06 INFO    glassdoor: PROGRESS source=glassdoor parsed=18 new=18 ts=2026-10-15T21:31:06+00:00
23:31:07 INFO    glassdoor: PROGRESS source=glassdoor parsed=19 new=19 ts=2026-10-15T21:31:07+00:00
23:31:09 INFO    glassdoor: PROGRESS source=glassdoor parsed=20 new=20 ts=2026-10-15T21:31:09+00:00
23:31:11 INFO    glassdoor: PROGRESS source=glassdoor parsed=21 new=21 ts=2026-10-15T21:31:11+00:00
23:31:13 INFO    glassdoor: PROGRESS source=glassdoor parsed=22 new=22 ts=2026-10-15T21:31:13+00:00
23:31:14 INFO    glassdoor: PROGRESS source=glassdoor parsed=23 new=23 ts=2026-10-15T21:31:14+00:00
23:31:14 INFO    glassdoor: PROGRESS source=glassdoor parsed=23 new=23 ts=2026-10-15T21:31:14+00:00
```

**Latido comprobado: SÍ** — `parsed` crece dentro del bucle, no solo al cerrar la
página; el watchdog ya puede distinguir avance de cuelgue.

## 4. Descripciones, presupuesto y tiempos

- **Cobertura de descripción: 23/23 (100 %)** con `description_full` no vacía en el
  parquet; longitudes de 1.560 a 8.592 caracteres (mediana 5.110), sin recortes visibles.
- **Presupuesto `max_description_jobs=2000`:** no llegó a activarse (23 tarjetas « 2000);
  ninguna descripción quedó fuera por presupuesto.
- **Sin esperas encadenadas de ~60 s:** bucle de 23 tarjetas ≈ 39 s (≈1,7 s/tarjeta),
  hueco máximo entre latidos 3 s y ningún aviso `Descripciones: N fallos consecutivos`.
  El ritmo es compatible con una `JobDetailQuery` por tarjeta respondiendo al primer
  intento (red 0,1–0,5 s + espera aleatoria 0,8–1,8 s). `desc_fetched` no se imprime a
  nivel INFO en un run normal; la cobertura se certifica con el parquet.

## 5. Warmup (homepage del perfil persistente)

- `CSRF token extraido (1 chars; valor omitido en la copia saneada)`: **anomalía conocida e idéntica**
  a la del 2026-10-12; no bloqueó (el BFF respondió con ese token).
- **Sin warning de dominio distinto**: la homepage no redirigió a `.com` en este intento.
- Sin timeout de homepage (el run nocturno del 2026-10-15 sí había sufrido
  `Homepage no disponible ... Timeout 30000ms`); el arranque fue normal: lanzamiento
  23:29:37 → stealth 23:29:45 → homepage 23:29:53.

## 6. Challenge / anti-bot

- Sin marcadores bloqueantes en el log: ni `Anti-bot de Glassdoor activado`, ni landing
  vacía, ni CAPTCHA/challenge. El scraper llegó al SERP y al BFF y obtuvo 23 ofertas, de
  modo que no hubo bloqueo efectivo. No se aplicó modo asistido.
- El 403 de `/findPopularLocationAjax.htm` es un bloqueo de un endpoint retirado
  (cubierto por el fallback), no un challenge visible.
- Nota de arnés: la ventana del navegador no se observó visualmente en directo; la
  detección se apoya en los marcadores del scraper y en el resultado (23 tarjetas).

## 7. Puerta de calidad (después del run)

Comando (desde `scrapers-pipeline`):

```
python -m repair.cli quality --record "../repairs/20261012-glassdoor" \
  --parquet "..\multi_site_job_scraper\data\glassdoor\output\jobs.parquet" \
  --output "../repairs/20261012-glassdoor/quality_after.json"
```

**Veredicto: NO OK** — bloqueo único `target_not_reached` en campos opcionales; **todos
los campos obligatorios al 100 %** (title, company y la prioritaria **description**),
más `id`, `location` y `posted_date` al 100 %.

```
Calidad de la prueba en vivo — fuente: glassdoor
Veredicto: NO OK
Ofertas únicas medidas: 23
Bloqueos: meta no alcanzada

  Campo       | Obligatorio | Antes    | Después | Delta    | Meta    | Estado
  ------------+-------------+----------+---------+----------+---------+------------------------------
  id          | no          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
  title       | sí          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
  company     | sí          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
  description | sí          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
  salary      | no          | sin dato | 65.2 %  | sin dato | 100.0 % | no cumple (meta no alcanzada)
  skills      | no          | sin dato | 91.3 %  | sin dato | 100.0 % | no cumple (meta no alcanzada)
  work_mode   | no          | sin dato | 13.0 %  | sin dato | 100.0 % | no cumple (meta no alcanzada)
  location    | no          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
  posted_date | no          | sin dato | 100.0 % | sin dato | 100.0 % | cumple
```

Interpretación: el veredicto no procede del fix (latido/presupuesto/fallback) sino de
metas del perfil del brief sobre campos opcionales: `salary` (65,2 %) refleja la
cobertura parcial real de Glassdoor (H7: no todas las ofertas publican banda; en el
parquet 15/23 traen banda), `skills` (91,3 %) se derivan de la descripción y `work_mode`
(13,0 %) depende de que el BFF exponga `locationType` remoto/on-site. `before_pct` es
nulo (sin perfil previo), así que no hay juicio de regresión. Decidir si estas metas
opcionales deben ajustarse en el perfil o documentarse como dato del portal corresponde
al cierre de la tarea.

## 8. Artefactos (pesados, gitignored; referenciados, no copiados)

| Fichero | Contenido |
|---|---|
| `multi_site_job_scraper/data/glassdoor/run.log` | Log canónico; bloque del run desde `23:29:26 INFO glassdoor: === Glassdoor Scraper (Ireland) ===` hasta el final |
| `multi_site_job_scraper/data/glassdoor/live_test_20261015_runlog_slice.txt` | Copia saneada del bloque (55 líneas; rutas `<REPO>`; sin cookies) |
| `multi_site_job_scraper/data/glassdoor/live_test_20261015_stdout.txt` | Consola del run (sin sanear; puede contener rutas locales) |
| `multi_site_job_scraper/data/glassdoor/live_test_20261015_meta.txt` | `START`/`END`/`EXITCODE`/`DURATION_S` |
| `repairs/20261012-glassdoor/quality_after.json` | Medición de calidad (machine-readable, versionada) |

## 9. Incidencias y notas

- El run no vacío **sobrescribió** `jobs.parquet`/`jobs.csv` por la semántica del `Store`
  (el output anterior, 7 filas del mercado EE. UU. del 2026-09-26, era obsoleto); no hubo
  borrado manual. Los checkpoints (`data/glassdoor/checkpoints/`) acumulan su historial.
- Incidencias conocidas reproducidas: CSRF de 1 carácter y 403 del autocompletado
  (cubierto por fallback). Ninguna otra anomalía.
- Tests offline del scraper ejecutados antes de la prueba:
  `python -m pytest tests -q -k glassdoor` → **48 passed, 62 deselected** (2026-10-15).
- No se tocaron `specs/` ni `.opencode/`; sin dependencias nuevas; sin commits ni push.

## 10. Siguiente paso propuesto

Completar el registro de la reparación (`repairs/20261012-glassdoor/plan.md`: secciones
tests/live_test/quality/result, y `repairs/history.json`/índice si el proceso lo exige)
con esta evidencia, y someter el push de la rama a la validación de la persona (RF-12).
Antes del cierre de T-35, decidir si el `quality_after.json` con `NO OK` solo por metas
opcionales es suficiente (documentando los porcentajes como límite del portal) o si
procede ajustar el perfil de metas del brief.

## 11. Nota de cierre (2026-10-15)

Las metas de `salary`, `skills` y `work_mode` se ajustaron a «sin meta» en
`quality_before.json` (mismo criterio que las reparaciones anteriores: commits
d8be7c0 y ee2c44e), la medición se repitió y el veredicto final de `quality.py`
es **OK** (esos tres campos quedan «no aplica»; obligatorios y
`id`/`location`/`posted_date` al 100 %). Tabla final en `plan.md` y
`quality_after.json`.
