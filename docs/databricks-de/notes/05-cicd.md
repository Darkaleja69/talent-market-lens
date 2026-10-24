# CI/CD y despliegue como código (Databricks Asset Bundles)

Nota de estudio de la spec 007 (ELT como código). Escrita al cerrar el track
(T-21), con lo practicado y lo aprendido; material de repaso, no documentación
operativa (eso está en `docs/RUNBOOK.md` §9 y en
`specs/007-ELT-As-Code/cutover.md`).

## Qué he practicado

- Llevar un job ELT real (7 tareas) a código: `databricks bundle generate job
  --existing-job-id … --key elt` como punto de partida y luego revisión a mano
  del recurso (T-06, T-07).
- Targets `dev` y `prod` con las mismas tareas y **variables por target**
  (catálogo, checkpoints, export, nombre); dev sin programación y prod con la cron
  real (T-07, T-15).
- CI que solo valida (`databricks bundle validate -t dev`) en PR/push, y
  deploy **solo a dev** por `workflow_dispatch` detrás del entorno `dev` de
  GitHub con *required reviewers* (T-12, T-14).
- Prueba rojo/verde del CI con un error intencionado y un `git revert` (T-13).
- Cutover de producción job manual → job del bundle, con dry-run previo y
  rollback documentado (T-15 a T-19; T-20 y T-22 quedan al cierre).

## Conceptos clave (en mis palabras)

- **Bundle = IaC del job.** `databricks.yml` define el bundle y sus targets;
  `resources/*.yml`, los recursos (aquí, un job con sus tareas, `depends_on`,
  compute, timeout y notificaciones); la `schedule` de producción se sobreescribe
  en el target `prod` de `databricks.yml`. El código de los notebooks vive
  en el repo y el bundle lo sincroniza al workspace.
- **`deploy` ≠ `run`.** `bundle deploy` sube ficheros y actualiza la definición;
  no ejecuta nada. Eso hace que un cutover sin doble ejecución sea solo un
  cambio de estado más un deploy.
- **Targets y variables.** El *cómo* es idéntico; el *dónde* (catálogo,
  checkpoints, export) y el *cuándo* (schedule) cambian por target. En
  producción, además, el **production mode** exige `run_as` y `permissions`.
- **`pause_status` es un override del target.** El job lo gestiona el bundle
  (`edit_mode: UI_LOCKED`): pausar o reanudar desde la UI no es estable, porque
  el siguiente `bundle deploy -t prod` re-aplica el valor de la definición. La
  forma fiable de cambiar el estado es editar, commitear, mergear y desplegar.
- **`sync.paths` vs `sync.include`.** `include` solo vuelve a incluir lo que
  `.gitignore` excluye; **no restringe** el sync. Para acotar lo que se sube hay
  que usar `sync.paths`.
- **Filtro de `paths` en CI.** Si un push/PR no toca las rutas filtradas, el
  workflow no corre; y al revertir un cambio, el diff de la PR queda vacío en
  esas rutas, así que el "verde" siguiente lo produce el trigger `push`.
- **Entorno `dev` de GitHub.** El deploy manual a dev pasa por un environment con
  aprobadores: automatización con puerta humana, sin secretos de prod en el
  runner.
- **Un cutover son dos operaciones ordenadas.** Primero se pausa el job antiguo,
  luego se habilita el nuevo; el inverso abre la puerta a una doble ejecución.
  El job viejo no se borra: es la pieza de rollback.

## Comandos y trucos

```powershell
# Bundle
databricks bundle validate -t dev --profile <perfil>
databricks bundle deploy   -t dev --profile <perfil>
databricks bundle summary  -t dev --profile <perfil>
databricks bundle generate job --existing-job-id <id> --key elt --profile <perfil>

# Estado y runs
databricks jobs get <job-id> --profile <perfil> --output json
databricks jobs list-runs --job-id <job-id> --limit 5 --profile <perfil> --output json
databricks jobs get-run <run-id> --profile <perfil> --output json
```

- Para leer tabla o metadatos sin abrir la UI: `databricks api post
  /api/2.0/sql/statements --json @body.json` (acepta `@fichero`, lo que evita
  pelearse con las comillas en PowerShell).
- Los epoch ms de la API se convierten a hora local con
  `[DateTimeOffset]::FromUnixTimeMilliseconds(...).ToOffset(...)`.
- Para ficheros de ADLS, un GET con SAS sirve como lectura de solo comprobación
  (`meta.json`), sin lanzar nada.

## Errores que he cometido y qué aprendí

- Asumir que `sync.include` acotaba el deploy: el primer deploy subió el repo
  entero. Lección: comprobar el tamaño/contenido del `files/` tras el deploy.
- Buscar `Fin pipeline` con `-Tail 5`: el log sigue creciendo después del
  cierre y las últimas líneas cambian; mejor `Select-String -Pattern`.
- Concatenar la SAS al `?` de la URL sin normalizar: si el token ya trae `?`,
  sale `??` y falla la autenticación (`TrimStart('?')`).
- Registrar horas a ojo: el fin real del run manual del 18-oct estaba 25 s antes
  de lo anotado. Lección: los tiempos, de la API (`run_duration`), no de la
  percepción.
- Validar de una sola vez al terminar el run también vale: el dry-run de las
  23:05 y la comprobación de las 23:10 se sustituyen por "list-runs + get-run"
  con el ciclo ya cerrado.

## Dudas pendientes

- Service principal como `run_as` en producción (hoy corre con el usuario) y qué
  permisos mínimos necesita sobre catálogo, storage y workspace.
- Sacar el correo de notificación de `resources/elt.job.yml` (repo público): a un
  secreto, a un parámetro de deploy o a un webhook.
- Deploy de producción automatizado: ¿tiene sentido con aprobación por
  environment, como el de dev, o se queda manual a propósito?
- Cómo encajar en el bundle los cambios de la 008/010 (upsert del bronce,
  rendimiento) sin tocar la programación de producción.
