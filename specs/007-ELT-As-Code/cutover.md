# Plan de cutover y rollback — spec 007 (ELT como código)

- **Estado:** **aprobado por la persona el 2026-10-18** para ejecutarlo esta
  misma noche (Opción B: después del run manual).
- **Redactado:** 2026-10-18.
- **Alcance:** pasar la fuente de verdad del job de producción del job manual
  `Git Based ELT` al job del Asset Bundle, sin doble ejecución y con rollback
  probado.
- **Ventana:** **domingo 18-oct-2026, tras terminar el run manual
  (≈23:20–23:50, Europe/Madrid)**.
  - Límite de aborto: **23:55**; el pipeline local de scrapers arranca a las
    00:00 (no bloquea el cutover, que no ejecuta nada, pero conviene cerrar
    antes).
  - Si algo no cuadra, rollback antes de las 00:00.
- **Dry-run (T-17):** **esta noche, 18-oct, a las 23:05**; el job del bundle
  sigue pausado mientras corre el manual. Se confirma que el bundle no dispara.
- **Primer run del job del bundle (T-19):** **lunes 19-oct a las 23:00**;
  verificación el martes 20 por la mañana.

> Horas en `Europe/Madrid`. El pipeline local de scrapers se lanza a las
> **00:00**; la ventana de esta noche termina antes de ese arranque. La ELT de
> las 23:00 procesa el landing que subió el pipeline en la madrugada anterior.
>
> Todos los comandos Git se ejecutan desde el worktree de la spec.

---

## 1. Referencias e IDs

| Elemento | Valor |
|---|---|
| Job manual (producción actual) | `704156530681034`, `Git Based ELT`, `UNPAUSED` |
| Job del bundle (producción) | `916863262593632`, `Git Based ELT`, `PAUSED` |
| Job del bundle (dev, referencia) | `320947022571947`, `[dev …] Git Based ELT Dev`, sin programación |
| Programación de prod | `50 0 23 * * ?`, `Europe/Madrid` (23:00 diaria) |
| Bundle / target | `talent-market-lens` / `prod` |
| Root del bundle prod | `/Workspace/Users/<user>/.bundle/talent-market-lens/prod` |
| Catálogo / checkpoints / export | `job_offers` / `…/_checkpoints` / `gold/web_export` |
| Backup del job manual | `specs/007-ELT-As-Code/job-actual.json` |
| Backup del job del bundle | `specs/007-ELT-As-Code/job-prod-bundle.json` |
| Perfil CLI | `vs_code` (`Valid: YES` el 2026-10-18) |
| CI | `.github/workflows/databricks-bundle-validate.yml` (solo dev) |

La CLI 1.21.0 está instalada. Para los comandos siguientes, define `$dbx` con
la ruta a `databricks.exe` (o deja `databricks` si está en el `PATH` de la
sesión):

```powershell
$dbx = "databricks"   # o la ruta completa a databricks.exe
```

Identidad de ejecución: se mantiene la persona como `run_as`, igual que el job
manual. El service principal queda como mejora documentada, no como requisito
del cutover.

---

## 2. Precondiciones (todas obligatorias)

### 2.1 Código y CI

- [ ] T-10 completada: run dev `540330014336242` en verde y conteos cotejados
      con prod (`evidence.md`).
- [ ] T-15 desplegada: job `916863262593632` existe, `PAUSED` y con **0 runs**.
- [ ] T-17 superada: pasada la hora programada (23:00) con el job pausado, el
      `jobs list-runs` del bundle sigue vacío (se comprueba en §5.1).
- [ ] `databricks bundle validate -t prod` → `Validation OK!`.
- [ ] Suites existentes en verde:
      `python -m pytest databricks_notebooks/tests -q` y
      `python -m pytest scrapers-pipeline/tests -q`.
- [ ] El merge de la rama a `main` (T-15..T-17 + `UNPAUSED`) se hace en §5.3,
      antes del deploy; `origin/main` queda actualizado con ese push.
- [ ] Antes de cerrar el merge: `git checkout main -- AGENTS.md` (el bloque
      local de trabajo guiado no debe llegar a `main`).
- [ ] Tras el merge de §5.3, CI verde sobre el commit de `main`; como gate
      local, `bundle validate -t prod` → `Validation OK!` antes del deploy.
- [ ] El commit de `main` que se despliega queda registrado en la tabla de
      ejecución (§10).

### 2.2 Workspace y jobs

- [ ] `vs_code` responde y es la identidad propietaria de ambos jobs.
- [ ] Job manual `704156530681034`: `UNPAUSED`, misma cron y zona, y su último
      run terminó en verde.
- [ ] Job del bundle `916863262593632`: `PAUSED`, misma cron y zona, 7 tareas,
      `timeout_seconds: 1800`, `max_concurrent_runs: 1`.
- [ ] Ningún run activo de ninguno de los dos jobs.
- [ ] Confirmado que no hay un tercer job programado a las 23:00 que pudiera
      duplicar la ELT.

Comprobación:

```powershell
& $dbx jobs get 704156530681034 --profile vs_code --output json
& $dbx jobs get 916863262593632 --profile vs_code --output json
& $dbx jobs list-runs --job-id 704156530681034 --limit 3 --profile vs_code --output json
& $dbx jobs list-runs --job-id 916863262593632 --limit 3 --profile vs_code --output json
& $dbx bundle summary -t prod --profile vs_code
```

### 2.3 Baseline de datos y export

Antes de tocar nada, guardar los conteos y el `meta.json` actuales:

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
ORDER BY tabla;
```

Y el export que consume la web (en un notebook del workspace, junto al
clúster que tiene acceso a ADLS):

```python
print(dbutils.fs.head(
    "abfss://landing@joboffershns.dfs.core.windows.net/gold/web_export/meta.json"))
```

Anotar `generated_at`, `data_date`, `tables` y `mode`. El baseline se guarda en
la tabla de §10 o en `evidence.md`.

### 2.4 Ventana y personas

- [ ] Ventana confirmada y sin otras tareas programadas ese día.
- [ ] Persona que ejecuta: deshabilita/rehabilita schedules en la UI y lanza el
      `bundle deploy`. El asistente lee evidencia y actualiza documentos; no
      despliega producción.
- [ ] Aprobación explícita registrada en §11.
- [ ] El pipeline local no está en marcha en la ventana (se lanza a las 00:00).

---

## 3. Backup

El cutover **no borra ni reescribe datos**: solo cambia qué job ejecuta la ELT
contra los mismos catálogos y checkpoints. No hace falta backup de tablas
(además Delta conserva el historial). El backup necesario es la definición:

| Qué | Dónde | Cómo se comprueba |
|---|---|---|
| Job manual | `job-actual.json` (T-03, saneado) | `jobs get 704156530681034` el día del cutover y cotejo de `settings` |
| Job del bundle | `job-prod-bundle.json` (T-15, saneado) | `jobs get 916863262593632` antes de la ventana |
| Definición del bundle | commit de `main` | `git rev-parse main` y `bundle summary -t prod` |
| Git folder | repo Git (`main`, commit `2dc4d25` el 2026-10-18; ver §9) | UI de la Git folder |

Si el job manual hubiera cambiado respecto a `job-actual.json`, **detener** y
actualizar el backup antes de continuar. Nunca se modifica ni se borra el job
manual durante el cutover: es la pieza de rollback.

> Nota (fuera del alcance de T-16): `resources/elt.job.yml` versiona un correo
> de notificación de fallo. Ya estaba en `main` y el cutover no lo cambia. Si
> se quiere dejar de publicar en el repo, es una decisión aparte (el repo es
> público) que debe tomarse antes o después del cutover.

---

## 4. Orden obligatorio

1. Deshabilitar el job manual.
2. Verificar que está pausado.
3. Habilitar el job del bundle (cambio de definición + deploy).
4. Verificar que está habilitado.
5. Esperar la ejecución real de las 23:00.

Nunca se invierte el orden: si el bundle se habilita antes de pausar el manual,
ambos podrían dispararse a las 23:00.

T-18 se ejecuta en la ventana; T-19 se verifica esa noche y a la mañana
siguiente; T-20 es el rollback en seco de la mañana siguiente. Sus evidencias
se commitean en la rama de la spec; el deploy de producción sale siempre de
`main`, y la rama se mergea al cerrar (T-22). El cambio de `pause_status` se
prepara en la rama y se mergea en la ventana, nunca se commitea directamente en
`main`.

El plan §10 describe un primer deploy de prod en pausa; T-15 ya dejó el job del
bundle desplegado y pausado, así que el cutover no repite ese paso: solo cambia
`pause_status` y verifica.

---

## 5. Pasos exactos

### 5.0 Resumen de deploys y runs (camino feliz)

| Momento | Acción | ¿Deploy? | ¿Run? |
|---|---|---|---|
| Domingo 18, 23:05 | Observar el dry-run del job pausado (T-17) | No | No (solo lectura) |
| Domingo 18, ≈23:20 | Confirmar que el run manual terminó `SUCCEEDED` | No | No |
| Domingo 18, ≈23:20 | Pausar el job manual en la UI | No | No |
| Domingo 18, ≈23:30 | **`bundle deploy -t prod` con `UNPAUSED`** | **Sí (1)** | No |
| Lunes 19, 23:00 | Ejecución programada del job del bundle (T-19) | No | **Sí, automática** |
| Martes 20, mañana | Verificar el run, conteos y `meta.json` | No | No |
| Martes 20, ventana (p. ej. 21:00) | **T-20:** deploy con `PAUSED` + reanudar el manual | **Sí (2)** | No |
| Martes 20, tras T-20 | Restaurar el cutover: deploy con `UNPAUSED` | **Sí (3)** | No |

Camino feliz: **3 deploys** (uno en T-18 y dos en T-20) y **0 ejecuciones
manuales**. El único run del job del bundle es el programado de las 23:00 del
lunes 19 (el manual ya habrá corrido el domingo); no lo lanza nadie a mano.

Solo si algo falla:

- Aborto antes de las 23:00: rollback con deploy `PAUSED` (§8.1). Sin run.
- Noche fallida o sin run: rollback (deploy `PAUSED`) y, si hace falta
  recuperar esa noche, `jobs run-now 704156530681034 --no-wait` **una vez**.
- Aborto después del merge y antes del deploy: reconciliar con un deploy en
  `PAUSED` (el job desplegado sigue pausado; `main` debe volver a `PAUSED`).

Las consultas de baseline/conteos y la lectura de `meta.json` son de solo
lectura (warehouse o notebook con acceso a ADLS); no lanzan la ELT.

### 5.1 Antes de la ventana (domingo 18, 23:05–23:20)

1. **T-17 (dry-run):** comprobar que el job del bundle sigue `PAUSED` y sin
   runs mientras el manual corre:

   ```powershell
   & $dbx jobs list-runs --job-id 916863262593632 --limit 3 --profile vs_code --output json
   & $dbx jobs list-runs --job-id 704156530681034 --limit 3 --profile vs_code --output json
   ```

   El bundle debe devolver `[]`; el manual debe mostrar el run de las 23:00.

2. Confirmar que el run manual termina `SUCCEEDED` antes de tocar nada.
3. **Baseline de §2.3** y comprobación de precondiciones de §2.2.
4. **No** cambiar todavía `pause_status`: el job del bundle sigue pausado y el
   manual sigue programado.

El merge de la spec a `main` y el cambio a `UNPAUSED` se hacen juntos en §5.3,
después de pausar el manual (una sola operación de Git en la ventana).

### 5.2 Inicio de la ventana: deshabilitar el job manual (≈23:20)

1. Confirmar que no hay runs activos (§2.2).
2. UI → **Workflows → Jobs → `Git Based ELT` (ID `704156530681034`) →
   Schedule → Pause**.
3. Verificar:

   ```powershell
   & $dbx jobs get 704156530681034 --profile vs_code --output json
   ```

   Debe mostrar `schedule.pause_status: PAUSED`. Anotar hora.

Si no queda pausado, **abortar** y volver a habilitarlo.

### 5.3 Habilitar el job del bundle y desplegar (≈23:25–23:45)

`main` solo recibe merges de la rama de la spec. El cambio de `PAUSED` a
`UNPAUSED` se prepara en `spec/007-elt-as-code` y se mergea en la ventana,
después de pausar el manual. Este merge lleva a `main` T-15, T-16, T-17 y el
commit `UNPAUSED` de una sola vez.

1. Volver a la rama de la spec y editar `databricks.yml`, target `prod`:

   ```powershell
   git switch spec/007-elt-as-code
   ```

   ```yaml
   schedule:
     pause_status: UNPAUSED
     quartz_cron_expression: 50 0 23 * * ?
     timezone_id: Europe/Madrid
   ```

2. Commit en la rama:

   ```powershell
   git add databricks.yml
   git commit -m "chore(bundle): habilita programacion de prod (cutover)"
   ```

3. Merge a `main` y push (requiere OK explícito), con el mismo patrón:

   ```powershell
   git switch main
   git pull
   git merge --no-ff --no-commit spec/007-elt-as-code
   git checkout main -- AGENTS.md
   git status
   git commit -m "Merge spec/007-elt-as-code: habilita programacion de prod"
   git push origin main
   ```

4. Validar y desplegar desde `main`. **`deploy` no ejecuta el job**; solo
   actualiza la definición:

   ```powershell
   & $dbx bundle validate -t prod --profile vs_code
   & $dbx bundle deploy -t prod --profile vs_code
   ```

5. Verificar:

   ```powershell
   & $dbx jobs get 916863262593632 --profile vs_code --output json
   & $dbx jobs list-runs --job-id 916863262593632 --limit 3 --profile vs_code --output json
   ```

   Debe mostrar `pause_status: UNPAUSED`, `50 0 23 * * ?`,
   `Europe/Madrid`, 7 tareas, `timeout_seconds: 1800`,
   `max_concurrent_runs: 1` y **0 runs**.

6. Confirmar de nuevo que el manual sigue `PAUSED` y sin runs nuevos.
7. Anotar en §10: hora, commit, IDs, resultado. Si algo no cuadra → §8.

### 5.4 Cierre de la ventana (≈23:50)

- [ ] Manual `PAUSED`.
- [ ] Bundle `UNPAUSED`, programado a las 23:00, sin runs.
- [ ] Sin runs activos.
- [ ] Baseline y estados registrados.
- [ ] La ventana termina sin dejar el sistema a medias. Si no se ha podido
      verificar todo, rollback antes de las 00:00 (el pipeline local arranca a
      esa hora).

---

## 6. Verificación de la noche (T-19: lunes 19 → martes 20)

### 6.1 Lunes 19, 23:05–23:15

```powershell
& $dbx jobs list-runs --job-id 916863262593632 --limit 5 --profile vs_code --output json
& $dbx jobs list-runs --job-id 704156530681034 --limit 3 --profile vs_code --output json
```

- Exactamente **un** run del bundle, lanzado por el schedule a las 23:00.
- **Ningún** run nuevo del job manual.
- Si a las 23:10 no hay run del bundle → abortar y rollback (§7, §8). Tras el
  rollback, si hace falta recuperar la noche, lanzar una vez el job manual con
  `run-now --no-wait` (§8.2).

### 6.2 Lunes 19, 23:15–23:30

```powershell
& $dbx jobs get-run <run_id> --profile vs_code --output json
```

- El run avanza; las 7 tareas se ejecutan en el orden esperado
  (bronze → 4 silver → gold → export).
- Duración esperada: ~18 min (referencia dev: 17 m 43 s).

### 6.3 Martes 20, a las 08:00

- [ ] El run terminó `SUCCEEDED` y sigue siendo el único de la noche.
- [ ] Las 7 tareas en verde; sin reintentos inesperados.
- [ ] Conteos de §2.3: las tablas **avanzan** (no caen) de forma coherente con
      el landing del día.
- [ ] `meta.json` del export tiene un `generated_at` nuevo y `data_date`
      actualizada; la web muestra datos frescos.
- [ ] El job manual sigue `PAUSED` y sin runs nuevos.
- [ ] El pipeline local de la madrugada del martes cerró con `Fin pipeline`
      (`logs/upload-<fecha>.log`).
- [ ] Registrar run id, hora, duración, conteos y `meta.json` en `evidence.md`.

Si algo falla → §7 y §8. No se reintenta a ciegas.

---

## 7. Criterios de aborto

Se aborta y se ejecuta el rollback si ocurre cualquiera de estos:

- Falla una precondición de §2 (CI, validate, backup, credenciales, merge).
- El job manual no queda `PAUSED` antes de habilitar el bundle.
- El job del bundle no queda `UNPAUSED` o su cron/zona no coinciden.
- Se detecta riesgo de doble ejecución (dos jobs habilitados a las 23:00).
- A las 23:10 del lunes 19 no hay run del bundle, o aparece un run del manual.
- El run falla, se queda colgado o supera el timeout de 30 min.
- Los conteos no avanzan, caen de forma inexplicable o el export no se
  actualiza.
- Aparece un run de dev escribiendo en recursos de prod, o viceversa.
- La persona decide abortar.

---

## 8. Rollback

### 8.1 Rollback inmediato (en la ventana del domingo 18)

El job del bundle es `edit_mode: UI_LOCKED` (gestionado por el bundle), así que
**no se depende del botón Pause de la UI**. La forma fiable de pausarlo es
cambiar la definición y desplegar.

Objetivo: pausa efectiva **< 5 min**; reconciliación completa (git + deploy)
**< 10 min**. Si el botón Pause del bundle estuviera disponible en la UI, puede
usarse como parada inmediata, pero siempre se reconcilia con el deploy de
`PAUSED`: el siguiente deploy re-aplicaría el valor de la definición.

1. En `spec/007-elt-as-code`, volver a `PAUSED`, commit, merge a `main`, push y
   desplegar (esto pausa el job):

   ```powershell
   git switch spec/007-elt-as-code
   # pause_status UNPAUSED -> PAUSED
   git add databricks.yml
   git commit -m "chore(bundle): revierte a prod pausado (rollback)"
   git switch main
   git pull
   git merge --no-ff --no-commit spec/007-elt-as-code
   git checkout main -- AGENTS.md
   git commit -m "Merge spec/007-elt-as-code: rollback a prod pausado"
   git push origin main
   & $dbx bundle deploy -t prod --profile vs_code
   ```

2. Verificar que el bundle quedó pausado:

   ```powershell
   & $dbx jobs get 916863262593632 --profile vs_code --output json   # PAUSED
   ```

3. UI → job manual `704156530681034` → Schedule → **Resume**.
4. Verificar ambos estados y que no hay runs activos:

   ```powershell
   & $dbx jobs get 704156530681034 --profile vs_code --output json   # UNPAUSED
   & $dbx jobs list-runs --job-id 916863262593632 --limit 1 --profile vs_code --output json
   & $dbx jobs list-runs --job-id 704156530681034 --limit 1 --profile vs_code --output json
   ```

5. Anotar hora y tiempos en §10 y `cutover.md`.

### 8.2 Rollback tras un run fallido o colgado

1. **No** lanzar el job manual mientras el run del bundle siga activo.
2. Esperar a que termine o se agote el timeout (30 min). Si a las 23:45 del
   lunes 19 sigue vivo, cancelarlo desde la UI/CLI.
3. Pausar el bundle y reanudar el manual (§8.1).
4. Verificar que no queda ningún run activo.
5. Recuperación opcional de la noche: una vez confirmado que no hay runs
   activos, lanzar **una** vez el job manual:

   ```powershell
   & $dbx jobs run-now 704156530681034 --profile vs_code --no-wait
   ```

   Es el comportamiento previo al cutover; úsese solo si se necesita recuperar
   los datos de esa noche. Sin `--no-wait`, la CLI espera a que el run termine
   (timeout por defecto de 20 min).
6. Analizar el fallo dentro de esta spec, corregir y repetir el dry-run antes de
   un nuevo cutover. No se abre otra spec para parchear este objetivo.

### 8.3 Prueba en seco (T-20)

En una ventana sin riesgo el martes 20 (tras verificar T-19) o el miércoles 21,
siempre antes de las 23:00:

1. Pausar el bundle y reanudar el manual (§8.1), cronometrando.
2. Verificar ambos estados y que no hay runs activos.
3. **Restaurar el estado de cutover**: pausar el manual, volver a `UNPAUSED` en
   la rama de la spec, mergear a `main`, desplegar y verificar. Cronometrar
   también la restauración.
4. Anotar los tiempos reales en §10 y corregir este documento si algo difiere.

---

## 9. Estado final de la Git folder

Observado el 2026-10-18:

- `talent-market-lens` es una Git folder (`is_git_folder: true`).
- Está en la rama **`main`**, pero en el commit `2dc4d25`, **74 commits por
  detrás** de `origin/main` (`6a2e9d4`). T-04 la vio en la rama de la 006; el
  merge de la 006 ya está en `main`.
- Mientras el job manual siga activo, **no se hace pull**: cambiaría el código
  que ejecuta producción antes del cutover.

Después de que T-19 confirme la noche en verde:

1. UI → Workspace → Git folder `talent-market-lens` → **Pull** hasta el `main`
   actual (o cambiar a `main` si algún día no lo estuviera). Si la carpeta tiene
   cambios locales, revisarlos o descartarlos antes del pull: no forman parte
   del repo y el job ya no los usa.
2. En la UI de la Git folder, confirmar que la rama es `main` y que el commit
   mostrado es el último de `origin/main`. Si hiciera falta, comprobar
   `.git/HEAD` y `.git/refs/heads/main` con `workspace export`.
3. Dejarla como **solo consulta/referencia**: el job del bundle ya no la usa.
   Comprobar que los 7 `notebook_path` del job `916863262593632` apuntan a
   `.bundle/talent-market-lens/prod/files/databricks_notebooks/…` y ninguno a la
   Git folder.
4. Opcional: retirar permisos de escritura o archivar la carpeta. No se borra:
   es referencia del código y de la historia.

---

## 10. Registro de ejecución

| Paso | Hora (Madrid) | Quién | ID / commit | Resultado |
|---|---|---|---|---|
| Dry-run T-17 (bundle 0 runs) | | | `916863262593632` | |
| Baseline de conteos y `meta.json` | | | | |
| Run manual del domingo `SUCCEEDED` | | | `704156530681034` | |
| Manual `PAUSED` | | | `704156530681034` | |
| Merge de la spec a `main` + push (T-15..T-17 + `UNPAUSED`) | | | | |
| CI verde sobre `main` | | | | |
| `bundle deploy -t prod` | | | | |
| Bundle `UNPAUSED` verificado | | | `916863262593632` | |
| Run de las 23:00 | | | | |
| Run `SUCCEEDED` + conteos + `meta.json` | | | | |
| Rollback en seco (T-20) | | | | |
| Restauración del cutover tras T-20 | | | | |
| Git folder actualizada a `main` | | | | |

---

## 11. Aprobación

Sin esta aprobación no se ejecuta T-18.

- **Aprobado por:** la persona (registro en la sesión de trabajo).
- **Fecha y hora:** 2026-10-18, ≈22:40 (Europe/Madrid).
- **Ventana confirmada:** domingo 18-oct-2026, tras el run manual
  (≈23:20–23:50). Opción B: dry-run esta noche y cutover a continuación.
- **Observaciones:** primer run del bundle el lunes 19 a las 23:00; T-19 el
  martes 20 por la mañana; T-20 en la siguiente ventana sin riesgo (martes 20 o
  miércoles 21).

Aprobación registrada también en `evidence.md` y en `tasks.md` (T-16).
