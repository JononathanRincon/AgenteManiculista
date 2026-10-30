# FASE_TRACKING.md — Quality Gate por Fases
## AgenteManiculista

> Quality gate oficial del proyecto. La **primera** fase marcada `[ ] ⏳` es el foco actual.
> **Regla de inmutabilidad:** no se borra ni recorta texto; solo cambian los marcadores de estado y
> se completan los marcadores `<...>`.

### Convenciones de estado
- `[ ] 🕓` Pendiente.
- `[ ] ⏳` En progreso.
- `[ ] ❌` Bloqueada: pruebas fallidas o gate incumplido. No se inicia la siguiente funcionalidad.
- `[x] ✅` Realizada y probada: código + pruebas en verde + cobertura medida.
- `[-] ⚠️ NO REALIZADA: <justificación>` Omitida o replanteada con justificación.

### Regla de cierre
Una fase solo se marca `[x] ✅` si **todos** sus sub-ítems están `[x]` o `[-]` justificados y se cumplen
los criterios de cierre. Antes de cerrar se reconcilia con `PLAN_IMPLEMENTACION.md` y `tasks.md`.

### Criterios de cierre de cualquier fase
1. Todas sus tareas en `tasks.md` están `[x]` o `[-]` justificadas.
2. 100 % de las pruebas en verde.
3. Cobertura de ramas **medida** con la herramienta: ≥ 85 % general y 100 % en disponibilidad, reservas,
   cola/worker, firma y máquina de estados (SDD §13.2).
4. Revisión de seguridad sin hallazgos críticos ni altos abiertos.
5. `docker compose build` exitoso.
6. Entrada de cierre registrada en `AI_PROCESS.md`.

---

## Fase 0 — SDD (compuerta humana)
> **Criterio de desbloqueo:** ninguno. Es la primera fase.

- [x] ✅ **0.0 `CONSTRAINTS.md` creado**
- [x] ✅ **0.1 SDD completo y aprobado por el usuario**
  - [x] 21 secciones del `SDD.md` completas
  - [x] Checklist de completitud §20.1 marcado
  - [x] Aprobación registrada en SDD §20.2 y en `AI_PROCESS.md`
  - Cierre: 2026-10-30 · versión SDD 0.3 · AI_PROCESS #7

## Fase 1 — Documentos derivados
> **Criterio de desbloqueo:** Fase 0 cerrada (SDD aprobado).

- [x] ✅ **1.1 Derivados del SDD**
  - [x] `requirements.md`
  - [x] `TDD.md`
  - [x] `design.md` con Blast Radius
  - [x] `docs/threat-model.md` y ADR iniciales
- [x] ✅ **1.2 Control y operación**
  - [x] `PLAN_IMPLEMENTACION.md` con mapeo de skills
  - [x] `tasks.md` (todo ítem del plan tiene tarea)
  - [x] `AGENTS.md` y `CLAUDE.md` del proyecto
  - [x] Revisión del usuario de los derivados
  - Cierre: 2026-10-30 · AI_PROCESS #8

## Fase 2 — Infraestructura base (Bloque 0)
> **Criterio de desbloqueo:** Fase 1 cerrada.

- [x] ✅ **2.1 Repositorio, Docker y CI**
  - [x] GitFlow (`main`, `develop`)
  - [x] pyproject, Makefile, ruff, mypy, pytest con cobertura
  - [x] Dockerfile y `docker-compose.yml`
  - [x] CI con lint, tipos, tests, auditoría de dependencias y escaneo de secretos
  - Tareas: TASK-01 … TASK-04
  - Cierre: 2026-10-30 · AI_PROCESS #11

## Fase 3 — Modelos y configuración (Bloque 1)
> **Criterio de desbloqueo:** Fase 2 cerrada con 100 % de tests en verde.

- [ ] ⏳ **3.1 Configuración, modelos y mensajes**
  - [ ] **Unitarias:** `tests/unit/test_config.py`, `test_settings.py`, `test_mensajes.py`
  - [ ] **Integración:** `tests/integration/test_modelos.py`
  - Tareas: TASK-05 … TASK-08, TASK-52
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 4 — Disponibilidad (Bloque 2)
> **Criterio de desbloqueo:** Fase 3 cerrada.

- [ ] 🕓 **4.1 Disponibilidad y puerto Calendario**
  - [ ] **Unitarias:** `tests/unit/test_disponibilidad.py`, `test_calendario_falso.py`
  - Tareas: TASK-09 … TASK-11, TASK-53
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida; objetivo 100 %)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 5 — Caché (Bloque 3)
> **Criterio de desbloqueo:** Fase 4 cerrada.

- [ ] 🕓 **5.1 Caché y espejo de eventos**
  - [ ] **Unitarias:** `tests/unit/test_cache.py`
  - [ ] **Integración:** `tests/integration/test_espejo.py`
  - Tareas: TASK-12, TASK-13, TASK-54
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 6 — Reservas, cola y worker (Bloque 4)
> **Criterio de desbloqueo:** Fase 5 cerrada.

- [ ] 🕓 **6.1 Reservas y concurrencia**
  - [ ] **Integración:** `test_reservas.py`, `test_concurrencia.py`
  - Tareas: TASK-14, TASK-15
- [ ] 🕓 **6.2 Cola, worker, expiración, cancelar y reagendar**
  - [ ] **Integración:** `test_cola.py`, `test_worker.py`, `test_expiracion.py`, `test_cancelar.py`, `test_reagendar.py`, `test_citas_futuras.py`
  - Tareas: TASK-16 … TASK-22, TASK-55
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida; objetivo 100 %)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 7 — Máquina de estados (Bloque 5)
> **Criterio de desbloqueo:** Fase 6 cerrada.

- [ ] 🕓 **7.1 Conversación y flujos**
  - [ ] **Unitarias:** `test_conversacion.py`, `test_domicilio.py`, `test_gestion.py`, `test_palabras_clave.py`, `test_dedup_aviso.py`
  - [ ] **E2E:** `tests/e2e/test_agendar_sin_llm.py`
  - Tareas: TASK-23 … TASK-29, TASK-56
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida; objetivo 100 %)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 8 — Simulador (Bloque 6)
> **Criterio de desbloqueo:** Fase 7 cerrada.

- [ ] 🕓 **8.1 Simulador `/demo`**
  - [ ] **Unitarias:** `test_transporte_simulado.py`
  - [ ] **E2E:** `tests/e2e/test_simulador.py`
  - Tareas: TASK-30 … TASK-34, TASK-57
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 9 — Capa de intención (Bloque 7)
> **Criterio de desbloqueo:** Fase 8 cerrada.

- [ ] 🕓 **9.1 Intención con LLM y fallback**
  - [ ] **Unitarias:** `test_intencion_esquema.py`, `test_intencion_orquestador.py`, `test_prompt_privacidad.py`, `test_llm_ninguno.py`, `test_llm_gemini.py`
  - [ ] **Evals:** `tests/evals/test_evals.py`
  - Tareas: TASK-35 … TASK-40, TASK-58
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 10 — Integración WhatsApp y observabilidad (Bloque 8)
> **Criterio de desbloqueo:** Fase 9 cerrada.

- [ ] 🕓 **10.1 Webhook, Graph API y salud**
  - [ ] **Integración:** `test_webhook.py`, `test_whatsapp_cliente.py`, `test_health.py`
  - [ ] **Unitarias:** `test_logging.py`
  - Tareas: TASK-41 … TASK-44, TASK-59
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida; firma 100 %)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 11 — Calendar real y push (Bloque 9)
> **Criterio de desbloqueo:** Fase 10 cerrada.

- [ ] 🕓 **11.1 Calendar real, push y modo degradado**
  - [ ] **Integración:** `test_calendario_google.py`, `test_push.py`, `test_modo_degradado.py`
  - Tareas: TASK-45 … TASK-47, TASK-60
  - Pruebas: pasadas <N> · fallidas 0 · cobertura ramas <N> % (medida)
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 12 — Documentación (Bloque 10)
> **Criterio de desbloqueo:** Fase 11 cerrada.

- [ ] 🕓 **12.1 README, manual de la clienta, runbook y CHANGELOG**
  - Tareas: TASK-48 … TASK-50
  - Cierre: <fecha> · AI_PROCESS #<N>

## Fase 13 — Lanzamiento
> **Criterio de desbloqueo:** todas las fases anteriores cerradas.

- [ ] 🕓 **13.1 Go/no-go**
  - [ ] `/agent-skills:ship` con decisión go
  - [ ] Despliegue de la demo con confirmación del usuario
  - [ ] Condiciones diferidas resueltas: PA-07, PA-09, PA-10, PA-15
  - [ ] Confirmación del usuario para release a `main`
  - Tareas: TASK-51, TASK-61, TASK-62
  - Cierre: <fecha> · versión `vX.Y.Z`
