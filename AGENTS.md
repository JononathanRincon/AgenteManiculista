# AGENTS.md — Constitución Operativa del Proyecto
## AgenteManiculista

> Léelo completo al arrancar **cualquier** sesión. Prevalece sobre las preferencias por defecto del
> agente en todo lo relativo a archivos, ramas y orden de trabajo. El criterio técnico y de seguridad lo
> define el skill `DevSenPy`. La vara de calidad está en `CONSTRAINTS.md` y no se rebaja.
> Donde dice "leer un archivo", usa `Read`. Asumir contenido de memoria está prohibido.

**Principio del producto (innegociable):** el LLM interpreta, nunca ejecuta. Toda decisión de
disponibilidad, escritura en Calendar y confirmación de cita la toma código determinista.

---

## 1. Flujo de inicio de sesión (obligatorio y secuencial)

0. **Compuerta del SDD:** verificar en `SDD.md` §20.2 el registro de aprobación (v0.3, 2026-10-30). Si falta, detenerse. Si faltan derivados, generarlos antes de la primera tarea.
1. **Fase activa:** abrir `FASE_TRACKING.md`; la primera fase `[ ] ⏳` es el foco.
2. **Tarea única:** primera `[ ]` de `tasks.md`. Prohibido saltar o ejecutar varias.
3. **Requisitos:** leer en `requirements.md` solo las US, EARS y NFR que cita la tarea. No leer `SDD.md` completo.
4. **Diseño:** leer en `design.md` la sección que cita la tarea. **Prohibido escribir código sin haberla leído.**
5. **Skill:** consultar `PLAN_IMPLEMENTACION.md` y aplicar el skill indicado (sección 7.5).
6. **Rama:** `git checkout develop` y `git checkout -b feature/TASK-<ID>-<nombre>`.
7. **Implementar** solo dentro del Blast Radius (sección 3).
8. **Probar:** ejecutar el test de la tarea; 100 % verde (sección 4).
9. **Integrar:** commit convencional y `merge --no-ff` a `develop` (sección 6).
10. **Mutar estado:** `[ ]` → `[x]` en `tasks.md` (y `FASE_TRACKING.md`/`PLAN_IMPLEMENTACION.md` al cerrar fase), respetando la sección 2.
11. **Auditar:** entrada en `AI_PROCESS.md` con la plantilla (sección 9).

---

## 2. Regla inquebrantable de inmutabilidad de líneas

Aplica a `PLAN_IMPLEMENTACION*.md`, `FASE_TRACKING.md` y `tasks.md`.
- **PROHIBIDO:** borrar, reordenar o recortar texto existente.
- **Permitido:** `[ ]` → `[x]` (implementada y 100 % en verde); `[ ]` → `[-] ⚠️ NO REALIZADA: <justificación>` (justificación al final de la línea); en `FASE_TRACKING.md` el indicador `🕓` → `⏳` → `✅`; completar marcadores `<...>`; agregar líneas nuevas al final de su bloque con ID nuevo.
- `AI_PROCESS.md` es **solo de anexado**.

---

## 3. Límites de modificación de código (Blast Radius)

La tabla vigente está en `design.md` §0 y prevalece sobre este resumen.

| Categoría | Archivos | Regla |
|---|---|---|
| Solo lectura | `SDD.md`, `requirements.md`, `TDD.md`, `design.md`, `AGENTS.md`, `CONSTRAINTS.md` | No modificar sin orden del usuario |
| Prohibidos | `.env`, `.env.*` (salvo `.env.example` sin valores), `*.pem`, `*.key`, credenciales de la service account | No leer, crear, modificar ni subir |
| Autorizadas | `app/`, `config/`, `tests/`, `docs/` | Crear y editar |
| Dependencias | `pyproject.toml`, lockfile | Solo con verificación en PyPI y justificación en `AI_PROCESS.md` |
| Infraestructura | `Dockerfile`, `docker-compose.yml`, `render.yaml`, `.github/workflows/`, `Makefile` | Solo en tareas de infraestructura |

Si una tarea requiere tocar algo fuera del Blast Radius: detenerse y pedir autorización.

---

## 4. Quality Gate obligatorio

1. Ejecutar el test de `tasks.md`. 2. 100 % en verde. 3. `ruff`, `mypy --strict` limpios y sin secretos en el diff. 4. Bitácora en `AI_PROCESS.md` con reporte cuantitativo (pasadas / fallidas / cobertura medida o "no medida").

Al cerrar fase o bloque: cobertura de ramas **medida** (≥ 85 % general; 100 % en disponibilidad, reservas, cola/worker, firma y máquina de estados), revisión de seguridad sin hallazgos críticos ni altos y `docker compose build` exitoso. Solo entonces `[x] ✅` en `FASE_TRACKING.md`.

---

## 5. Archivos del harness
`AGENTS.md` (constitución) · `CLAUDE.md` (importa este archivo) · `CONSTRAINTS.md` (vara de calidad) · `tasks.md` (tarea inmediata) · `requirements.md` (solo lo que cita la tarea) · `design.md` (esquemas, contratos, Blast Radius) · `TDD.md` (solo si `design.md` no resuelve) · `PLAN_IMPLEMENTACION.md` (qué skill) · `FASE_TRACKING.md` (gates) · `AI_PROCESS.md` (auditoría) · `SDD.md` (referencia pasiva, no leer completo).

---

## 6. GitFlow

| Rama | Origen | Destino | Regla |
|---|---|---|---|
| `main` | — | — | Producción; solo recibe `release/*` o `hotfix/*`; prohibido commit directo |
| `develop` | `main` | — | Integración; prohibido commit directo |
| `feature/TASK-<ID>-<nombre>` | `develop` | `develop` | Una por micro-tarea, tras el Quality Gate |
| `release/vX.Y.Z` | `develop` | `main` y `develop` | **Requiere confirmación del usuario** |
| `hotfix/<nombre>` | `main` | `main` y `develop` | **Requiere confirmación del usuario** |

Commits convencionales (`feat`, `fix`, `test`, `refactor`, `docs`, `chore`). Integración:
`git merge --no-ff feature/TASK-XX-… -m "merge: feature/TASK-XX-… a develop tras 100% tests en verde"`.
`git push`, merges a `main`, tags y despliegues: **siempre con confirmación explícita del usuario**.

---

## 7. Loop Engineering y contexto Just-In-Time

- **Kickoff de bloque:** al iniciar un bloque de `tasks.md`, leer solo su sección en `design.md` y sus US/EARS/NFR. Dentro del bloque no se releen en cada tarea (Zero-Read Loop); en sesión nueva se repite el kickoff.
- **Bucle interno (TDD):** test que falla primero → código → test. Si falla, analizar y corregir. **Máximo 3 intentos consecutivos**; si no converge, detenerse, reportar la causa raíz y registrar el intento. Prohibido borrar, saltar (`skip`) o debilitar un test.
- **Bucle externo:** commit + merge `--no-ff` → mutar `[x]` → `AI_PROCESS.md` → siguiente tarea del **mismo bloque**. Al cerrar el bloque: reconciliar `tasks.md`, `PLAN_IMPLEMENTACION.md` y `FASE_TRACKING.md`, **pausar y notificar** al usuario.
- Trabajo que no corresponde a una tarea: primero agregar la tarea con ID nuevo. Prohibido trabajar fuera del backlog.
- **APIs externas:** antes de escribir código contra Graph API, Calendar API, `google-genai` o SQLAlchemy/SQLite, consultar la documentación oficial (`source-driven-development`) y anotarlo en `AI_PROCESS.md`. No inventar endpoints ni campos.

### 7.5 Comandos de `agent-skills` en el loop
| Momento | Comando o skill |
|---|---|
| Requisito nuevo o idea difusa | Volver al SDD (`/agent-skills:spec`); el usuario re-aprueba |
| Tareas nuevas | `/agent-skills:plan` |
| Contratos y puertos | `api-and-interface-design` |
| Duda de skill | `using-agent-skills` |
| Implementar | `/agent-skills:build` (`incremental-implementation`) |
| Test que falla / verde / bug (Prove-It) | `/agent-skills:test` |
| Fallo del bucle interno | `debugging-and-error-recovery` |
| Librerías, versiones, APIs | `source-driven-development` |
| Commit y merge | `git-workflow-and-versioning` |
| Cierre de bloque | `/agent-skills:review` con `code-reviewer`, `security-auditor`, `test-engineer` en paralelo; `/agent-skills:code-simplify` |
| Pipeline y Docker | `ci-cd-and-automation` |
| Logs, métricas | `observability-and-instrumentation` |
| UI del simulador | `frontend-ui-engineering`, `browser-testing-with-devtools` |
| Decisión de alto riesgo | `doubt-driven-development` y ADR |
| Lanzamiento | `/agent-skills:ship` |

`/agent-skills:build auto` solo dentro del bloque activo y sin saltarse el protocolo (rama, gate al 100 %, commit, mutación de estado, entrada en `AI_PROCESS.md`); al terminar el bloque se pausa y se notifica.

---

## 8. Docker
Todo corre en contenedor: multi-stage, usuario no root, `HEALTHCHECK`, imagen base fijada, secretos inyectados en ejecución. **Una sola instancia y un solo proceso** (`--workers 1`): el worker único y SQLite lo exigen. Especificación en `design.md` §5.

---

## 9. Plantilla de entrada en AI_PROCESS.md
Una entrada por interacción, en orden cronológico, nunca se modifican entradas anteriores. Campos: modelo, fase/tarea, prompt literal, output, archivos, patrones, comandos, Git, dependencias, reporte de pruebas, casos, intentos del bucle, estado actualizado y pendientes.

---

## 10. Reglas específicas del producto
- El LLM nunca recibe teléfono, nombre, dirección, historial ni identificadores; solo texto, catálogo, fecha de hoy y nombre del estado.
- Ninguna escritura en Calendar se decide con datos cacheados; la verificación en vivo ocurre en el worker.
- `CONFIRMANDO` solo se alcanza por toque en una hora ofrecida.
- Todo texto visible vive en `config/mensajes.yaml`.
- Teléfono siempre enmascarado en logs; nunca tokens ni el contenido enviado al LLM.
- Despliegue a Render, push y release: solo con confirmación explícita del usuario.
