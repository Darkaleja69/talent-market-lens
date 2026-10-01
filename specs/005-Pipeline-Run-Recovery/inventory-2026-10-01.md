# Inventario de restos preservados — run truncado 2026-10-01

- **Tarea:** T-01 (RF-2, RF-7).
- **Fecha de preservación:** 2026-10-01 22:56:16 (+02:00).
- **Fecha de verificación de integridad:** 2026-10-01 23:13 (+02:00).
- **Directorio preservado:**
  `C:\Users\aleja\AppData\Local\Temp\opencode\pending-recovery-2026-10-01`.

## Propósito

Estos cuatro ficheros son los restos válidos del run truncado del 2026-10-01
(Indeed y Multi-site; InfoJobs no generó datos en ese run y LinkedIn ya se
publicó). Su copia se conserva fuera del árbol del repositorio para que los
runs posteriores, la limpieza o los `cleanup` no los sobrescriban ni borren, y
es la **referencia inmutable de la comprobación real de T-17**.

## Comprobación de integridad

Comandos usados (PowerShell 5.1):

```powershell
$copy = "C:\Users\aleja\AppData\Local\Temp\opencode\pending-recovery-2026-10-01"
Get-ChildItem -LiteralPath $copy -File -Force                      # contenido real (sin ocultos ni subdirs)
Get-Item    -LiteralPath <fichero>                                 # tamaño y LastWriteTime
Get-FileHash -LiteralPath <fichero> -Algorithm SHA256              # hash de copia y original
```

La copia contiene exactamente los 4 ficheros de la tabla (no hay subdirectorios
ni ficheros ocultos). Para cada uno se calculó el SHA256 de la copia y del
original en el repositorio.

| # | Fichero en la copia | Origen en el repositorio | Bytes | LastWriteTime (local, copia y original) | SHA256 copia | SHA256 original | ¿Coincide? |
|---|---|---|---|---|---|---|---|
| 1 | `indeed_jobs_20261001_0001.parquet` | `indeed_jobs_scraper\output\indeed_jobs_20261001_0001.parquet` | 62 306 | 2026-10-01T02:10:47.3737267+02:00 | `83C13F416970F533749E06FD3E875E4326E376E41E64B57274E7E6D057F8FD22` | `83C13F416970F533749E06FD3E875E4326E376E41E64B57274E7E6D057F8FD22` | Sí |
| 2 | `irishjobs_jobs.parquet` | `multi_site_job_scraper\data\irishjobs\output\jobs.parquet` | 352 949 | 2026-10-01T04:47:53.8319244+02:00 | `9813DF3E281613DFA4B74D6F446F04B60610507E1E1779800F5C16FB5EEDFAC1` | `9813DF3E281613DFA4B74D6F446F04B60610507E1E1779800F5C16FB5EEDFAC1` | Sí |
| 3 | `multi_site_jobs_unified.parquet` | `multi_site_job_scraper\data\merged\jobs_unified.parquet` | 372 077 | 2026-10-01T04:50:20.9751885+02:00 | `E2099E7BEABE3453919F363B4A384AA92CDA255507169F34EB57BB17FAB3D4DD` | `E2099E7BEABE3453919F363B4A384AA92CDA255507169F34EB57BB17FAB3D4DD` | Sí |
| 4 | `multi_site_last_run.json` | `multi_site_job_scraper\data\merged\last_run.json` | 855 | 2026-10-01T04:50:23.4996998+02:00 | `F21B7F3064050430970B06031E3C9B5776A90F5D1C489A4C92585EF4CC0E914D` | `F21B7F3064050430970B06031E3C9B5776A90F5D1C489A4C92585EF4CC0E914D` | Sí |

**Resultado:** 4/4 ficheros presentes, con tamaño, `LastWriteTime` y SHA256
idénticos entre copia y original. **No hubo que reparar nada**: ningún fichero
faltaba ni difería. Los cuatro originales siguen existiendo en el repositorio en
la fecha de verificación.

## Notas

- La copia se hizo el 2026-10-01 a las 22:56:16 (+02:00); la hora de modificación
  de cada copia es la del original, que es lo que interesa para la
  reconstrucción del día real de cada dato (`multi_site_last_run.json` conserva
  `run_at=2026-10-01T04:50:23`; `irishjobs` quedó escrito a las 04:47:53; Indeed
  a las 02:10:47).
- `multi_site_last_run.json` documenta el cierre del wrapper huérfano: merge
  parcial (solo `irishjobs`), `stepstone_nl` detenido por watchdog
  (`watchdog_no_progress`) y `global_exit=1`. Ver `evidence-2026-10-01.md`.
- **Mientras no se ejecute la comprobación real (T-17), estos restos no deben
  borrarse ni sobrescribirse** (plan §2 y §9). Si algún original desapareciera
  por la limpieza o por un run posterior, la copia preservada sigue siendo la
  fuente.
- Si en el futuro la copia se alterase, se debe volver a copiar desde el
  original mientras exista; si el original ya no existiera, se conserva la copia
  y se anota la pérdida (en esta comprobación no ocurrió).
