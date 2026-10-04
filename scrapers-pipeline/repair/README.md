# Reparación de scrapers — guía operativa

Núcleo determinista de la reparación de scrapers (spec 004): convierte el
diagnóstico diario (spec 001) en objetivos reparables, umbral incremental,
puerta de calidad y registros versionados. La investigación y el cambio de
código los hacen los agentes; esta CLI solo coordina entradas y salidas. Diseño
completo: `specs/004-Repair-Failed-Scrapers/plan.md` (§3.1, §4, §5, §9).

Todos los comandos se ejecutan **desde `scrapers-pipeline/`** (como
`verification.verify_run`) con la forma
`python -m repair.cli <subcomando> [opciones]`.

## Paso 0 — refrescar el diagnóstico antes de seleccionar

`logs/diagnostic_last.json` puede estar desfasado: el ejecutable de
verificación se lanza a mano y el pipeline nocturno no lo regenera. Antes de
seleccionar objetivos, ejecuta desde `scrapers-pipeline/`:

```powershell
python -m verification.verify_run --offline
```

- Regenera el diagnóstico sobre el último run terminado con **escritura
  atómica** (nunca se lee un fichero a medias); `--offline` omite la landing de
  Azure, que no forma parte de la reparación.
- Si el diagnóstico resultante sigue siendo más antiguo que el último
  `logs/upload-*.log`, `targets` lo avisa por stderr (`Aviso: ...`) sin fallar;
  decide entonces si hay un run incompleto. Solo regenera la salida de la 001:
  no toca su código ni su contrato (RF-15).

## Contrato de entrada

Entrada: `scrapers-pipeline/logs/diagnostic_last.json` con `schema_version: 1`;
incluye el `run` de origen, `global_status`, `sources[]` (`id`, `kind`,
`status`, `outcome`, ofertas, `completeness[]`, `publication`, `failures`,
`evidence`) e `investigations[]`. Contrato completo:
`specs/004-Repair-Failed-Scrapers/plan.md` §4.

- `--diagnostic RUTA` acepta una ruta explícita; sin él se usa la ruta por
  defecto (`logs/diagnostic_last.json`, anclada al paquete). Un diagnóstico
  ausente, ilegible, sin `schema_version: 1` o con `global_status`
  `inconclusive` es un error de dominio (salida 1) y no inicia reparación; los
  avisos de desfase no cambian la salida (el comando termina con 0).

## Subcomandos

| Subcomando | Sintaxis | Salida |
|---|---|---|
| `targets` | `targets [--diagnostic RUTA]` | cola priorizada en español |
| `brief` | `brief --source ID [--field CAMPO] [--output RUTA] [--diagnostic RUTA]` | brief JSON en inglés |
| `threshold` | `threshold [--source ID] [--history RUTA]` | umbral incremental por fuente |
| `quality` | `quality --record DIRECTORIO --parquet RUTA [--output RUTA]` | tabla antes/después y veredicto |
| `record` | `record --brief FICHERO [--on-date YYYY-MM-DD]` o `record --record DIRECTORIO --status {probado,descartado,escalado} --result TEXTO [--changes\|--tests\|--live-test\|--verified-offers\|--quality-after]` | apertura o cierre del registro |

### `targets` — cola de objetivos

Lee el diagnóstico y presenta en español la cola priorizada: fuentes `failed`
(primarias) y, después, las investigaciones de completitud (secundarias). Los
`id` y los códigos de máquina se mantienen en inglés.

```powershell
python -m repair.cli targets
```

La salida empieza con `Objetivos de reparación — run de origen: <fecha>` y
`Total: N objetivos (P primarios, S secundarios)`.

### `brief` — contexto del objetivo

Construye el JSON en inglés (fuente, run, motivo, evidencia y rutas, playbook,
perfil y metas de calidad, alcance de prueba); con `--output` lo escribe y lo
anuncia, y sin él lo imprime. Si una fuente tiene varios candidatos (un
primario y sus investigaciones de campo), `--field CAMPO` desambigua; sin él
falla listándolos (salida 1).

```powershell
python -m repair.cli brief --source infojobs
python -m repair.cli brief --source stepstone_nl --field salary
python -m repair.cli brief --source infojobs --output "brief-infojobs.json"
```

### `threshold` — umbral incremental

Con `--source` muestra `fuente | mejor verificado: N | umbral: M`; sin él lista
las fuentes del historial (una línea por fuente) y, si está vacío, avisa de
que el umbral de cualquier fuente es 1 (`max(1, mejor verificado + 1)` sin
historial); por defecto lee `repairs/history.json` (raíz del repositorio). Ejemplo:
`python -m repair.cli threshold --source infojobs` →
`infojobs | mejor verificado: sin dato | umbral: 1`.

### `quality` — puerta de calidad

Aplica la puerta de calidad al parquet de la prueba en vivo contra el perfil
`quality_before.json` del registro: obligatorios (`title`, `company`,
`description`) al 100 %, sin regresión de más de 5 puntos y metas alcanzadas.
Imprime la tabla antes/después en español; con `--output` escribe el veredicto
JSON (lo habitual es `<registro>/quality_after.json`, que `record --record`
reutiliza si no se pasa `--quality-after`). La salida es **0 solo si la puerta
pasa**; si no, imprime la tabla y sale con 1.

```powershell
python -m repair.cli quality --record "../repairs/20261003-infojobs" `
  --parquet "infojobs_jobs_scraper/data/live_test.parquet" `
  --output "../repairs/20261003-infojobs/quality_after.json"
```

### `record` — abrir y cerrar el registro

- **Abrir:** `record --brief FICHERO` crea `repairs/<YYYYMMDD>-<fuente>/` con
  `plan.md` (estado `planificado`), `context.json`, `quality_before.json` y
  `evidence/`, a partir del `run_date` del brief; `--on-date` fija la fecha de
  apertura (solo con `--brief`). No sobrescribe un registro existente e imprime
  directorio, rama del fix y estado (crear la rama git es paso del orquestador,
  plan §5).
- **Cerrar:** `record --record DIRECTORIO --status ... --result TEXTO` completa
  `plan.md` (cambios, tests, prueba en vivo, calidad y resultado) y actualiza
  el índice `repairs/README.md` y el historial `repairs/history.json`. Una
  reparación `probado` exige `--verified-offers N` (entero ≥ 1) y sube el
  listón; `descartado` y `escalado` se registran sin bajarlo. `--quality-after
  FICHERO` es opcional: si se omite, se reutiliza el `quality_after.json` ya
  guardado en el registro.

```powershell
python -m repair.cli record --brief "brief-infojobs.json"
python -m repair.cli record --record "../repairs/20261003-infojobs" `
  --status probado --result "3 ofertas con umbral 1" --verified-offers 3
```

Estos dos comandos escriben en `repairs/`: úsalos solo en una reparación real.

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | Éxito; también `quality` con veredicto que pasa la puerta |
| 1 | Error de dominio (diagnóstico, brief, registro, calidad NO OK) |
| 2 | Error de uso de argparse (opciones ausentes o incorrectas) |

## Ciclo previsto

El proceso encaja en el ciclo **run nocturno → verificación (paso 0) →
reparación**. Dentro de la reparación, el flujo del plan §5 es: `targets`
(elegir alcance) → `brief` y apertura del registro → investigación con el
subagente `web-inspector` → implementación → verificación → prueba en vivo
acotada → `quality` → cierre del registro → **validación humana del push**
(RF-12). Durante la reparación no se sube a Azure, no se toca la landing, no se
ejecuta el merge de Multi-site y no se hace push ni merge sin la validación
humana; la única ejecución de scrapers permitida es la prueba en vivo acotada.
En la primera versión el ciclo se lanza a mano.

## Dónde queda todo

- `repairs/README.md`: índice de reparaciones (se actualiza solo).
- `repairs/history.json`: historial de recuentos verificados por fuente
  (inglés, nunca decrece).
- `repairs/<YYYYMMDD>-<fuente>/`: registro de cada reparación (`plan.md`,
  `context.json`, `quality_before.json`, `quality_after.json`, `evidence/`).
  `repairs/example/` es la plantilla didáctica, no un registro real.

Los registros se versionan en la rama del fix y llegan a `main` con su merge; el detalle completo está en `specs/004-Repair-Failed-Scrapers/`.
