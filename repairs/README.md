# Registros de reparaciones

Esta carpeta guarda el registro versionado de cada reparación de scraper de la
spec 004: qué falló, qué se investigó, qué se cambió, qué se probó y con qué
calidad quedó. Hay un registro por fuente y reparación, en
`repairs/<YYYYMMDD>-<fuente>/` (RF-2), creado al abrir la rama del fix y
completado al terminar (RF-11). `repairs/example/` es un ejemplo didáctico de
la plantilla; no es un registro real y no interfiere con los registros de las
reparaciones.

## Estructura

(según el plan técnico §3.4)

```
repairs/
  README.md              # este índice
  history.json           # historial de recuentos verificados por fuente (inglés)
  <YYYYMMDD>-<fuente>/
    plan.md              # plan + resultado (español)
    context.json         # brief generado (inglés)
    evidence/            # capturas, recortes de log, respuestas y resúmenes saneados
    quality_before.json  # perfil de calidad del diagnóstico (inglés)
    quality_after.json   # medición del test en vivo y delta (inglés)
```

- `plan.md` sigue la plantilla `scrapers-pipeline/repair/record_template.md`;
  sus secciones se actualizan con `repair.records` (T-14) sin perder lo ya
  escrito.
- `history.json` lo mantiene `repair.threshold` (RF-10); nunca decrece.
- El registro se versiona en la rama del fix y llega a `main` con su merge.
- Los extractos y capturas van saneados: sin credenciales ni tokens, y sin
  rutas de perfiles de navegador (RF-11); las rutas de usuario se escriben como
  `<HOME>`.

## Ramas

Cada fix se trabaja en su propia rama `repair/<fuente>-<YYYYMMDD>`, creada
desde `main` (durante el primer caso real de la 004 se crea desde la rama de la
spec, porque el proceso vive en ella; plan §3.4). Cada fuente y reparación
queda aislada de las demás (RF-14). El push de la rama requiere la validación
de la persona (RF-12): hasta entonces el sistema no hace push, ni merge a
`main`, ni abre pull requests.

## Índice

La tabla la actualiza `repair.records` (T-14) de forma determinista dentro de
los marcadores `repair-index:start` y `repair-index:end`; no añadas ni edites
filas a mano. Convención de cada fila:

- `fecha`: fecha del run de origen, `YYYY-MM-DD`.
- `fuente`: id de la fuente en inglés (`infojobs`, `stepstone_nl`, ...).
- `estado`: `planificado`, `probado`, `descartado` o `escalado`.
- `rama`: `repair/<fuente>-<YYYYMMDD>`.
- `resultado`: resumen corto en español (p. ej. «3 ofertas con umbral 1»).
- `calidad`: veredicto de `quality_after.json` (`OK` o `NO OK`) y un resumen
  breve en español (p. ej. «OK: obligatorios 100 %, sin regresión»).

<!-- repair-index:start -->
| fecha | fuente | estado | rama | resultado | calidad |
|---|---|---|---|---|---|
| 2026-10-05 | infojobs | probado | repair/infojobs-20261005 | 4 ofertas verificadas (umbral 1) en la prueba acotada; modo CDP validado: Chrome lanzado directo + conexión del scraper sin challenge | OK: cumplen id, title, company, description, work_mode, location, posted_date |
<!-- repair-index:end -->
