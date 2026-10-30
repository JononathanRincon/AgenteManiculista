# PLAN_IMPLEMENTACION.md — Hoja de Ruta y Mapeo de Skills
## AgenteManiculista

> Guía de alineación entre el usuario y el agente, derivada del `SDD.md` aprobado (§13.1).
> **Regla de inmutabilidad:** no se borra ni recorta texto; solo cambian los marcadores
> `[ ]` → `[x]` o `[ ]` → `[-] ⚠️ NO REALIZADA: <justificación>`.
> **Regla de cobertura:** todo ítem de este plan tiene al menos una tarea en `tasks.md`.
> Los comandos `/agent-skills:*` cargan solos sus skills. Flujo típico: spec → plan → build → test → review → ship.
> Correspondencia: Fase N+2 ↔ Bloque N de `tasks.md` (Fase 2 = Bloque 0, Fase 3 = Bloque 1, …).

---

## 1. Mapeo de skills, comandos y agentes

| Dominio / tipo de tarea | Skill, comando o agente | Cuándo se usa |
|---|---|---|
| Decidir qué skill aplicar | `using-agent-skills` | Cuando la tarea no indica skill o hay duda |
| Criterio técnico y seguridad | `devsenpy` | Siempre; prevalece ante conflicto |
| Estándar de calidad | `constraint-driven-development` | `CONSTRAINTS.md` |
| Desglose en micro-tareas | `planning-and-task-breakdown`, `/agent-skills:plan` | Tareas nuevas |
| Gestión de contexto | `context-engineering` | Kickoff de bloque, AGENTS.md |
| Implementación | `incremental-implementation`, `/agent-skills:build` | Una tarea a la vez; `build auto` solo dentro del bloque (AGENTS.md §7.5) |
| TDD y bugs | `test-driven-development`, `/agent-skills:test` | Test que falla primero; Prove-It en bugs |
| Documentación oficial | `source-driven-development` | **Siempre** antes de usar Graph API, Calendar API, `google-genai`, SQLAlchemy/SQLite |
| Contratos y puertos | `api-and-interface-design` | Puertos `Calendario`, `Mensajeria`, `ProveedorLLM` |
| Depuración | `debugging-and-error-recovery` | Fallos del bucle (máx. 3 intentos) |
| Seguridad | `security-and-hardening`, agente `security-auditor` | Webhook, firma, secretos, prompt injection |
| Rendimiento | `performance-optimization` | NFR-REN-01/02 |
| Pruebas en navegador | `browser-testing-with-devtools` | UI de `/demo` (requiere MCP de Chrome DevTools) |
| UI del simulador | `frontend-ui-engineering` | Bloque 6 |
| Cobertura y estrategia de pruebas | agente `test-engineer` | Cierre de bloque |
| Revisión de código | `/agent-skills:review`, `code-review-and-quality`, agente `code-reviewer` | Cierre de bloque, con los agentes en paralelo |
| Simplificación | `code-simplification`, `/agent-skills:code-simplify` | Refactor |
| Observabilidad | `observability-and-instrumentation` | Logs, métricas, `/health` |
| CI/CD y Docker | `ci-cd-and-automation` | Pipeline y contenedores |
| Ramas y versiones | `git-workflow-and-versioning` | GitFlow |
| Decisiones y ADR | `documentation-and-adrs`, `doubt-driven-development` | Decisiones de alto riesgo |
| Prompts del LLM | `arquitecto-de-prompts` | `config/prompt_intencion.txt` versionado |
| Migraciones | `deprecation-and-migration` | Cambios de esquema con datos reales |
| Lanzamiento | `shipping-and-launch`, `/agent-skills:ship` | Go/no-go |

---

## 2. Fases del plan

### Fase 0 — Gobierno y SDD (compuerta humana)
*Skills:* `constraint-driven-development`, `interview-me`, `spec-driven-development`, `devsenpy`
- [x] **0.0** `CONSTRAINTS.md` (una sola vez, desde `devsenpy`)
- [x] **0.1** Levantamiento con el usuario (contexto, roles, flujos, datos, normativa)
- [x] **0.2** `SDD.md` completo con sus 21 secciones
- [x] **0.3** Validación del usuario: checklist §20.1 y aprobación en §20.2 (SDD v0.3, 2026-10-30)

### Fase 1 — Documentos derivados
*Skills:* `api-and-interface-design`, `source-driven-development`, `documentation-and-adrs`, `planning-and-task-breakdown`, `context-engineering`
- [x] **1.1** `requirements.md` derivado del SDD
- [x] **1.2** `TDD.md` derivado del SDD
- [x] **1.3** `design.md` con Blast Radius
- [x] **1.4** `docs/threat-model.md` y ADR iniciales
- [x] **1.5** `CONSTRAINTS.md` ajustado con el stack aprobado
- [x] **1.6** `tasks.md`, `FASE_TRACKING.md`, `AGENTS.md` y `CLAUDE.md`
- [x] **1.7** Revisión del usuario de los derivados

### Fase 2 — Infraestructura base (Bloque 0)
*Skills:* `ci-cd-and-automation`, `git-workflow-and-versioning`, `devsenpy`
- [x] **2.1** Repositorio con GitFlow (`main`, `develop`) y estructura de carpetas
- [x] **2.2** `pyproject.toml`, Makefile, ruff, mypy, pytest y cobertura
- [x] **2.3** Dockerfile y `docker-compose.yml` con healthcheck
- [x] **2.4** CI con lint, tipos, tests, cobertura, auditoría de dependencias y escaneo de secretos

### Fase 3 — Modelos y configuración (Bloque 1)
*Skills:* `incremental-implementation`, `test-driven-development`, `source-driven-development`
- [x] **3.1** Carga y validación de YAML (negocio, servicios, domicilio)
- [x] **3.2** Settings de entorno y `.env.example`
- [x] **3.3** Modelos ORM, engine SQLite (WAL, `BEGIN IMMEDIATE`)
- [x] **3.4** Mensajes externalizados
- [x] **3.5** Revisión del bloque y cobertura medida

### Fase 4 — Disponibilidad (Bloque 2)
*Skills:* `incremental-implementation`, `test-driven-development`, `api-and-interface-design`
- [x] **4.1** Cuadrícula y bloques ocupados
- [x] **4.2** Horarios ofrecibles
- [x] **4.3** Puerto `Calendario` y `CalendarioFalso`
- [x] **4.4** Revisión del bloque y cobertura medida

### Fase 5 — Caché (Bloque 3)
*Skills:* `incremental-implementation`, `test-driven-development`, `source-driven-development`
- [x] **5.1** Caché con TTL, métricas e invalidación
- [x] **5.2** Espejo de eventos con `syncToken`
- [x] **5.3** Revisión del bloque y cobertura medida

### Fase 6 — Reservas, cola y worker (Bloque 4)
*Skills:* `incremental-implementation`, `test-driven-development`, `security-and-hardening`, `debugging-and-error-recovery`
- [x] **6.1** Reserva transaccional con bloques UNIQUE
- [x] **6.2** Prueba de concurrencia (carrera)
- [x] **6.3** Cola, worker, verificación en vivo, backoff y pausa
- [x] **6.4** Expiración, cancelar y reagendar
- [x] **6.5** Consulta de citas y límite por clienta
- [x] **6.6** Revisión del bloque con `code-reviewer`, `security-auditor`, `test-engineer` en paralelo y cobertura medida

### Fase 7 — Máquina de estados (Bloque 5)
*Skills:* `incremental-implementation`, `test-driven-development`
- [x] **7.1** Estados y transiciones persistidos
- [x] **7.2** Flujo de agendar con botones y listas
- [x] **7.3** Modalidad domicilio con recargo
- [x] **7.4** Reagendar y cancelar
- [x] **7.5** Palabras clave, FAQ, aviso de datos y deduplicación
- [x] **7.6** E2E de agendar sin LLM
- [x] **7.7** Revisión del bloque y cobertura medida

### Fase 8 — Simulador (Bloque 6)
*Skills:* `frontend-ui-engineering`, `browser-testing-with-devtools`, `test-driven-development`
- [x] **8.1** Transporte y calendario simulado
- [x] **8.2** API `/demo`
- [x] **8.3** Interfaz `/demo` vertical presentable
- [x] **8.4** Botón de carrera
- [x] **8.5** Configuración de despliegue de la demo (sin desplegar)
- [x] **8.6** Revisión del bloque

### Fase 9 — Capa de intención (Bloque 7)
*Skills:* `arquitecto-de-prompts`, `source-driven-development`, `security-and-hardening`, `test-driven-development`
- [x] **9.1** Esquema Pydantic de intención
- [x] **9.2** Puerto `ProveedorLLM` y proveedor `none`
- [x] **9.3** Adaptador Gemini
- [x] **9.4** Orquestador palabras clave → LLM → fallback con métrica
- [x] **9.5** Test de privacidad del prompt
- [x] **9.6** Evals
- [x] **9.7** Revisión del bloque

### Fase 10 — Integración WhatsApp (Baileys, v0.4) y observabilidad (Bloque 8)
*Skills:* `source-driven-development`, `security-and-hardening`, `observability-and-instrumentation`
- [x] **10.1** Verificación GET y firma HMAC
- [x] **10.2** POST con 200 inmediato, background y deduplicación
- [x] **10.3** Cliente Graph con reintentos
- [x] **10.4** `/health` y logging JSON enmascarado
- [x] **10.5** Revisión del bloque
- [x] **10.6** (v0.4, ADR-009) Transporte Baileys en Python: humanización, menús numerados, debounce, handoff humano, rate limit, webhook del sidecar y adaptador
- [x] **10.7** (v0.4, ADR-009) Sidecar Node.js con Baileys y su contenedor
- [x] **10.8** (v0.4, ADR-009) Revisión del Bloque 8 con Baileys

### Fase 11 — Calendar real y push (Bloque 9)
*Skills:* `source-driven-development`, `security-and-hardening`, `test-driven-development`
- [x] **11.1** Adaptador Google Calendar
- [x] **11.2** `events.watch`, `/webhook/calendar` y renovación
- [x] **11.3** Modo degradado
- [x] **11.4** Revisión del bloque
- [x] **11.5** Cableado de producción (TASK-73)
- [x] **11.6** Correcciones de la auditoría del bloque (TASK-74)

### Fase 12 — Documentación (Bloque 10)
*Skills:* `documentation-and-adrs`
- [x] **12.1** README técnico con diagrama Mermaid
- [x] **12.2** Manual para la clienta
- [x] **12.3** Runbook y CHANGELOG

### Fase 13 — Lanzamiento
*Skills:* `shipping-and-launch`, `ci-cd-and-automation`, `observability-and-instrumentation`
- [ ] **13.1** `/agent-skills:ship` con decisión go/no-go
- [ ] **13.2** Despliegue de la demo en Render (con confirmación del usuario)
- [ ] **13.3** Release `vX.Y.Z` a `main` con confirmación del usuario
