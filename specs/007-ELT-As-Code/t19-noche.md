# T-19 — Verificación de la noche (lunes 19 → martes 20, 2026)

Hoja de trabajo para cerrar T-19. Los resultados se registran en `evidence.md`
(fila + sección «T-19 — Noche real») y en `cutover.md` §10; este fichero es la
guía de comandos y la plantilla para rellenar, no la evidencia final.

- **Estado de partida:** bundle `916863262593632` `UNPAUSED` desde el
  2026-10-18 23:24:40, cron `50 0 23 * * ?`/`Europe/Madrid`, 7 tareas, 0 runs.
  Manual `704156530681034` `PAUSED` desde el 2026-10-18 23:21.
- **Baseline:** commit `265b19f`, capturado el 2026-10-19 21:53–21:55
  (`evidence.md`, sección «T-19 — Baseline»).
- **Criterios:** `cutover.md` §6 y §7. Si algo falla → §8.1 (rollback) y
  análisis en esta spec; **no** se reintenta a ciegas.

```powershell
$dbx = "databricks"   # o la ruta completa a databricks.exe
```

---

## 1. Arranque — 23:05 a 23:15

```powershell
& $dbx jobs list-runs --job-id 916863262593632 --limit 5 --profile vs_code --output json
& $dbx jobs list-runs --job-id 704156530681034 --limit 3 --profile vs_code --output json
```

- [ ] **Exactamente un** run del bundle, lanzado por la programación (anotar
      `trigger`/`run_type` tal como venga; la hora exacta suele ser ~23:00).
- [ ] **Ningún** run nuevo del job manual (el último sigue siendo
      `364055615164423`, del 2026-10-18).
- [ ] Si a las **23:10** no hay run del bundle → abortar: rollback (§8.1),
      análisis del fallo en la spec y, solo si se necesita recuperar la noche,
      `run-now` del manual **una vez** (§8.2, acción no read-only).

| Qué | Valor observado |
|---|---|
| Hora de la observación | |
| Run id del bundle | |
| `trigger` / `run_type` | |
| Hora de inicio (`start_time`) | |
| Runs nuevos del manual | |

## 2. Avance — 23:15 a 23:30

```powershell
& $dbx jobs get-run <run_id> --profile vs_code --output json
```

- [ ] Estado `RUNNING` o `TERMINATED/SUCCESS`; 7 tareas en el orden
      bronze → 4 silver → gold → export.
- Referencia del run dev (`540330014336242`, 17 m 43 s): bronce 7 m 36 s,
  indeed 4 m 50 s, infojobs 8 m 46 s, linkedin 6 m 11 s, multisite 5 m 24 s,
  gold 55 s, export 26 s.
- Si a las **23:45** sigue vivo, cancelarlo (§8.2). No se lanza el manual
  mientras el run del bundle esté activo.

## 3. Cierre del ciclo (tras el run)

```powershell
& $dbx jobs get-run <run_id> --profile vs_code --output json
& $dbx jobs list-runs --job-id 916863262593632 --limit 5 --profile vs_code --output json
& $dbx jobs list-runs --job-id 704156530681034 --limit 3 --profile vs_code --output json
& $dbx jobs get 704156530681034 --profile vs_code --output json
```

Conteos (misma consulta que el baseline; `cutover.md` §2.3):

```sql
SELECT 'silver.offers_linkedin' AS tabla, count(*) AS filas FROM job_offers.silver.offers_linkedin
UNION ALL SELECT 'silver.offers_multi_site', count(*) FROM job_offers.silver.offers_multi_site
UNION ALL SELECT 'silver.offers_indeed', count(*) FROM job_offers.silver.offers_indeed
UNION ALL SELECT 'silver.offers_infojobs', count(*) FROM job_offers.silver.offers_infojobs
UNION ALL SELECT 'silver.companies_linkedin', count(*) FROM job_offers.silver.companies_linkedin
UNION ALL SELECT 'silver.companies_multi_site', count(*) FROM job_offers.silver.companies_multi_site
UNION ALL SELECT 'silver.companies_indeed', count(*) FROM job_offers.silver.companies_indeed
UNION ALL SELECT 'silver.companies_infojobs', count(*) FROM job_offers.silver.companies_infojobs
UNION ALL SELECT 'gold.fact_offers', count(*) FROM job_offers.gold.fact_offers
UNION ALL SELECT 'gold.fact_offer_skills', count(*) FROM job_offers.gold.fact_offer_skills
UNION ALL SELECT 'gold.companies_complete_catalog', count(*) FROM job_offers.gold.companies_complete_catalog
UNION ALL SELECT 'gold.dim_currency', count(*) FROM job_offers.gold.dim_currency
UNION ALL SELECT 'gold.dim_skill_list', count(*) FROM job_offers.gold.dim_skill_list
UNION ALL SELECT 'gold.dim_calendar', count(*) FROM job_offers.gold.dim_calendar
ORDER BY tabla
```

`meta.json` del export (consulta GET; el token no se imprime):

```powershell
$sas = $env:LANDING_SAS_TOKEN.TrimStart('?')   # admite el token con o sin '?' inicial
$url = "https://joboffershns.blob.core.windows.net/landing/gold/web_export/meta.json?" + $sas
try {
  (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60).Content
} catch {
  "ERROR al leer meta.json: " + $_.Exception.InnerException.Message
}
```

| Tabla | Baseline 19-oct 21:53 | Después | Δ | Lectura |
|---|---:|---:|---:|---|
| silver.offers_linkedin | 11.734 | | | |
| silver.offers_multi_site | 9.533 | | | |
| silver.offers_indeed | 2.748 | | | esperado ≈0 (pipeline 19-oct en `no_data`) |
| silver.offers_infojobs | 2.287 | | | |
| silver.companies_linkedin | 3.881 | | | |
| silver.companies_multi_site | 4.312 | | | |
| silver.companies_indeed | 1.311 | | | esperado ≈0 o solo nuevas |
| silver.companies_infojobs | 956 | | | |
| gold.fact_offers | 24.563 | | | |
| gold.fact_offer_skills | 42.200 | | | |
| gold.companies_complete_catalog | 9.454 | | | |
| gold.dim_currency | 19 | | | |
| gold.dim_skill_list | 95 | | | |
| gold.dim_calendar | 2.907 | | | |

`meta.json`:

| Campo | Baseline | Después |
|---|---|---|
| `generated_at` | `2026-10-18T21:18:50Z` | |
| `data_date` | `2026-10-17` | |
| `mode` | `full` | |
| `size_bytes` | 3.345.996 | |
| `tables.fact_offers` | 24.563 | |
| `tables.fact_offer_skills` | 42.200 | |
| `tables.dim_calendar` | 2.907 | |
| `tables.dim_skill_list` | 95 | |

Checklist de cierre (`cutover.md` §6.3):

- [ ] El run terminó `SUCCEEDED` y sigue siendo el **único** de la noche.
- [ ] 7 tareas en verde, sin reintentos inesperados.
- [ ] Los conteos **avanzan** (no caen) de forma coherente con el landing del
      día; indeed puede no moverse (0 ficheros subidos el 19).
- [ ] `meta.json` con `generated_at` nuevo (criterio duro) y `tables` = gold;
      `data_date` (= `max(posted_date)`) puede no moverse (ver nota de
      `cutover.md` §2.3).
- [ ] El manual sigue `PAUSED` (`jobs get` → `schedule.pause_status`) y sin runs
      nuevos.
- [ ] El pipeline local de la madrugada que alimenta **este** ciclo cerró
      `Fin pipeline` (en el ciclo del 19-oct es `upload-2026-10-19.log`; el
      `upload-2026-10-20.log` corresponde al ciclo siguiente):
      `Select-String -Path C:\Users\<user>\Documents\projects\scrapers-pipeline\logs\upload-2026-10-1*.log -Pattern "Fin pipeline"`
      (si hubo fallos, anotar cuáles y si son preexistentes).
- [ ] La web pública muestra datos frescos (proxy: `meta.json` con `tables` =
      gold y `generated_at` nuevo).
- [ ] Git folder `talent-market-lens`: pull a `main` (§9, acción no read-only) y
      comprobar que los 7 `notebook_path` del job apuntan a
      `.bundle/talent-market-lens/prod/...`.

## 4. Qué necesito para cerrar T-19

1. Salida de **`list-runs` del bundle** (para demostrar que el run es el único
   de la noche) y de **`list-runs` del manual** (para demostrar que no se
   disparó).
2. Salida de `get-run` del run (estado, duración y duración de cada tarea).
3. Salida de `jobs get 704156530681034` (que el manual siga `PAUSED`).
4. Los 14 conteos de después (o la tabla rellena).
5. El `meta.json` nuevo.
6. La confirmación del `Fin pipeline` del ciclo (19-oct) y del pull de la Git
   folder.

Con eso actualizo `evidence.md` (fila + sección), `cutover.md` §1 (estado
post-cutover), §10 y `tasks.md` (T-19 → `[x]`), y hago el commit de cierre.

## 5. Si algo falla

- No lanzar el manual mientras el run del bundle siga activo.
- Rollback: `cutover.md` §8.1 (deploy `PAUSED` + reanudar manual) y §8.2 si el
  run falló o se colgó.
- Registrar hora, síntoma y decisión; el análisis del fallo se hace en esta
  spec (no se abre otra para parchear este objetivo).
