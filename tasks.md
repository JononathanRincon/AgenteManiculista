# tasks.md — Backlog Táctico de Micro-tareas
## AgenteManiculista

> **Instrucciones para el agente:**
> 1. Tomar **la primera** tarea con `[ ]` y solo esa. Prohibido saltar o ejecutar varias a la vez.
> 2. En el kickoff del bloque, leer solo las secciones de `requirements.md` y `design.md` que cita.
> 3. Una rama `feature/TASK-<ID>-<nombre>` por tarea.
> 4. Ejecutar el **test** indicado antes de marcar `[x]`; debe ser un comando ejecutable.
> 5. Registrar la tarea en `AI_PROCESS.md` y actualizar `FASE_TRACKING.md` al cerrar el módulo.
> 6. Antes de usar una API externa (Graph, Calendar, `google-genai`, SQLAlchemy/SQLite), consultar la documentación oficial (`source-driven-development`) y anotarlo en `AI_PROCESS.md`.
>
> **Regla de inmutabilidad:** no se borra ni recorta texto; solo cambian los marcadores
> `[ ]` → `[x]` o `[ ]` → `[-] ⚠️ NO REALIZADA: <justificación>`. Tareas nuevas se agregan al
> final de su bloque con ID nuevo. Todo ítem de `PLAN_IMPLEMENTACION.md` tiene al menos una tarea.
> Cada tarea dura entre 20 y 30 minutos. Los bloques cierran con una tarea de revisión (IDs 52 a 60).
> Fase N+2 del plan ↔ Bloque N.

## Formato de tarea

```markdown
- [ ] **TASK-XX** · <título imperativo>
  - Objetivo · Trazabilidad · Diseño · Skill · Archivos · Entregable · Test · Hecho cuando
```

---

## Bloque 0 — Infraestructura (Fase 2)
*Skills:* `git-workflow-and-versioning`, `ci-cd-and-automation`

- [x] **TASK-01** · Inicializar repositorio con GitFlow y estructura de carpetas
  - Objetivo: repositorio con `main` y `develop`, trabajo en `develop`, estructura de TDD §2.
  - Trazabilidad: NFR-SEG-02, NFR-DES-01 · Plan: 2.1
  - Diseño: design.md §0, §1.1
  - Skill: `git-workflow-and-versioning`
  - Archivos: raíz del proyecto, `.gitignore`
  - Entregable: `.gitignore` (bloquea `.env*` salvo `.env.example`, `*.pem`, `*.key`, `*.db`), carpetas `app/ config/ tests/ docs/`, primer commit con los documentos del harness
  - Test: `git branch --list main develop`
  - Hecho cuando: existen `main` y `develop` y el primer commit no contiene secretos.

- [x] **TASK-02** · Configurar pyproject, Makefile, ruff, mypy y pytest con cobertura de ramas
  - Objetivo: herramientas de calidad de `CONSTRAINTS.md` operativas con versiones verificadas.
  - Trazabilidad: NFR-CAL-01 · Plan: 2.2
  - Diseño: TDD §1, §8; design.md §0 (Dependencias)
  - Skill: `/agent-skills:build`, `source-driven-development`
  - Archivos: `pyproject.toml`, lockfile, `Makefile`, `tests/__init__.py`
  - Entregable: targets `install`, `run`, `test`, `lint`; versiones fijadas tras verificar PyPI y justificadas en `AI_PROCESS.md`
  - Test: `make lint && make test`
  - Hecho cuando: lint y tipos pasan en un proyecto vacío con un test trivial y la cobertura de ramas se mide.

- [x] **TASK-03** · Crear Dockerfile multi-stage y docker-compose de desarrollo
  - Objetivo: contenedor único, no root, con `HEALTHCHECK`.
  - Trazabilidad: NFR-DES-01 · Plan: 2.3
  - Diseño: design.md §5
  - Skill: `ci-cd-and-automation`
  - Archivos: `Dockerfile`, `docker-compose.yml`, `.env.example`
  - Entregable: imagen que arranca `uvicorn --workers 1` con volumen `/data`; `.env.example` comentado sin valores
  - Test: `docker compose build`
  - Hecho cuando: la imagen construye y declara `HEALTHCHECK` y usuario no root.

- [x] **TASK-04** · Crear pipeline de CI
  - Objetivo: ruff, mypy, pytest con cobertura, pip-audit y escaneo de secretos en cada push.
  - Trazabilidad: NFR-SEG-01, NFR-CAL-01 · Plan: 2.4
  - Diseño: TDD §10
  - Skill: `ci-cd-and-automation`
  - Archivos: `.github/workflows/ci.yml`, `Makefile`
  - Entregable: workflow y target `make ci` equivalente en local
  - Test: `make ci`
  - Hecho cuando: `make ci` pasa localmente y el workflow replica los mismos pasos.

## Bloque 1 — Modelos y configuración (Fase 3)
*Skills:* `incremental-implementation`, `test-driven-development`, `source-driven-development`

- [x] **TASK-05** · Cargar y validar `negocio.yaml` y `servicios.yaml` con domicilio parametrizable
  - Objetivo: configuración inmutable validada al arrancar; domicilio habilitado exige recargo.
  - Trazabilidad: US-09, US-06, EARS-CFG-01, EARS-CFG-02, EARS-DOM-05 · Plan: 3.1
  - Diseño: design.md §B1.1, §B1.2, §B1.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/config.py`, `config/negocio.yaml`, `config/servicios.yaml`, `tests/unit/test_config.py`
  - Entregable: `cargar_config()`, modelos Pydantic `Negocio`, `Servicio`, `Domicilio`, `ConfiguracionInvalida`
  - Test: `pytest tests/unit/test_config.py -q`
  - Hecho cuando: YAML inválido o domicilio sin recargo impiden el arranque con mensaje claro.

- [x] **TASK-06** · Definir `Settings` de entorno y `.env.example` completo
  - Objetivo: variables validadas (`PROVEEDOR_LLM`, `SIMULADOR`, `GEMINI_TIER`, etc.) y documentadas.
  - Trazabilidad: NFR-SEG-02, EARS-INT-05 · Plan: 3.2
  - Diseño: design.md §5 (variables), §B1.2
  - Skill: `/agent-skills:build`
  - Archivos: `app/config.py`, `.env.example`, `tests/unit/test_settings.py`
  - Entregable: `Settings` con valores por defecto seguros; `.env.example` con el origen de cada variable
  - Test: `pytest tests/unit/test_settings.py -q`
  - Hecho cuando: `.env.example` no contiene valores reales y toda variable requerida está comentada.

- [x] **TASK-07** · Crear modelos ORM y engine SQLite con WAL y `BEGIN IMMEDIATE`
  - Objetivo: tablas de design.md §2 y escritura serializada.
  - Trazabilidad: NFR-INT-01, EARS-COL-01 · Plan: 3.3
  - Diseño: design.md §1.3, §2, §B1.4
  - Skill: `/agent-skills:test`, `source-driven-development`
  - Archivos: `app/modelos.py`, `tests/integration/test_modelos.py`
  - Entregable: modelos, tipo de fecha UTC, `crear_engine()`, `unidad_de_trabajo()`
  - Test: `pytest tests/integration/test_modelos.py -q`
  - Hecho cuando: PRAGMAs activos y una segunda escritura espera mientras otra tiene `BEGIN IMMEDIATE`.

- [x] **TASK-08** · Externalizar mensajes en `mensajes.yaml` y renderizador
  - Objetivo: todo texto visible desde YAML, validado al arrancar.
  - Trazabilidad: NFR-MNT-01, EARS-CFG-01 · Plan: 3.4
  - Diseño: design.md §1.2, §B1.1, §B1.4
  - Skill: `/agent-skills:build`
  - Archivos: `app/mensajes.py`, `config/mensajes.yaml`, `tests/unit/test_mensajes.py`
  - Entregable: `texto(clave, **vars)`, `CLAVES_REQUERIDAS`, mensajes en español colombiano tuteando (incluye borrador del aviso de datos)
  - Test: `pytest tests/unit/test_mensajes.py -q`
  - Hecho cuando: falta una clave requerida y el arranque falla; un test detecta strings visibles hardcodeados en `app/`.

- [x] **TASK-52** · Revisión del Bloque 1 y cobertura medida
  - Objetivo: cerrar el bloque con revisión y cobertura de ramas medida.
  - Trazabilidad: NFR-CAL-01 · Plan: 3.5
  - Diseño: design.md §B1.5
  - Skill: `/agent-skills:review`, `/agent-skills:code-simplify`
  - Archivos: `app/config.py`, `app/modelos.py`, `app/mensajes.py`
  - Entregable: informe de revisión en `AI_PROCESS.md`; hallazgos críticos/altos resueltos
  - Test: `make test`
  - Hecho cuando: 100 % en verde y cobertura de ramas medida dentro del umbral.

## Bloque 2 — Disponibilidad (Fase 4)
*Skills:* `incremental-implementation`, `test-driven-development`, `api-and-interface-design`

- [x] **TASK-09** · Calcular la cuadrícula y los bloques ocupados por servicio
  - Objetivo: `bloques_ocupados` con duración + margen + desplazamiento redondeado a bloques.
  - Trazabilidad: US-02, EARS-DIS-02, EARS-DOM-01 · Plan: 4.1
  - Diseño: design.md §2.3, §B2.2, §B2.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/disponibilidad.py`, `tests/unit/test_disponibilidad.py`
  - Entregable: función pura `bloques_ocupados`
  - Test: `pytest tests/unit/test_disponibilidad.py -q -k bloques`
  - Hecho cuando: 45→2, 75→3, 150→6, 30→2 con margen 15 y el desplazamiento suma bloques.

- [x] **TASK-10** · Calcular horarios ofrecibles (máx. 10)
  - Objetivo: respetar apertura/cierre, anticipación, ventana, días laborales y ocupación.
  - Trazabilidad: US-02, EARS-DIS-01, EARS-DIS-03, EARS-DIS-04 · Plan: 4.2
  - Diseño: design.md §B2.2, §B2.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/disponibilidad.py`, `tests/unit/test_disponibilidad.py`
  - Entregable: función pura `horarios_ofrecibles`
  - Test: `pytest tests/unit/test_disponibilidad.py -q -k ofrecibles`
  - Hecho cuando: la duración debe terminar antes del cierre y nunca se devuelven más de 10.

- [x] **TASK-11** · Definir el puerto `Calendario` y `CalendarioFalso`
  - Objetivo: contrato del dominio con Calendar y doble de pruebas con fallos programables (429, 5xx, 410).
  - Trazabilidad: EARS-COL-05, EARS-CAC-05 · Plan: 4.3
  - Diseño: design.md §B3.2, §B6.2
  - Skill: `api-and-interface-design`
  - Archivos: `app/puertos.py`, `app/simulador/calendario_falso.py`, `tests/unit/test_calendario_falso.py`
  - Entregable: `Protocol Calendario`, tipos `Intervalo`, `EventoNuevo`, `EventoCita`, errores de calendario, `CalendarioFalso`
  - Test: `pytest tests/unit/test_calendario_falso.py -q`
  - Hecho cuando: el falso cuenta llamadas, simula 410/429 y respeta `id` de evento duplicado (409).

- [x] **TASK-53** · Revisión del Bloque 2 y cobertura medida
  - Objetivo: cerrar el bloque con revisión y 100 % de ramas en `disponibilidad`.
  - Trazabilidad: NFR-CAL-01 · Plan: 4.4
  - Diseño: design.md §B2.5
  - Skill: `/agent-skills:review`
  - Archivos: `app/disponibilidad.py`, `app/puertos.py`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y cobertura 100 % de ramas en `disponibilidad`.

## Bloque 3 — Caché (Fase 5)
*Skills:* `incremental-implementation`, `test-driven-development`, `source-driven-development`

- [x] **TASK-12** · Implementar el caché de disponibilidad con TTL, métricas e invalidación
  - Objetivo: hit/miss, TTL configurable e invalidación por día.
  - Trazabilidad: US-02, EARS-CAC-01, EARS-CAC-06, NFR-REN-02 · Plan: 5.1
  - Diseño: design.md §1.5, §B3.2, §B3.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/cache.py`, `app/metricas.py`, `tests/unit/test_cache.py`
  - Entregable: `CacheDisponibilidad`, contadores `cache_hit`/`cache_miss`
  - Test: `pytest tests/unit/test_cache.py -q`
  - Hecho cuando: 10 consultas del mismo día producen 1 llamada al calendario.

- [x] **TASK-13** · Mantener el espejo de eventos con `syncToken` y resincronización ante 410
  - Objetivo: traer solo lo que cambió y recuperarse del token vencido.
  - Trazabilidad: US-08, EARS-CAC-05 · Plan: 5.2
  - Diseño: design.md §B3.2, §B3.4
  - Skill: `/agent-skills:test`, `source-driven-development`
  - Archivos: `app/cache.py`, `tests/integration/test_espejo.py`
  - Entregable: sincronización incremental sobre el puerto `Calendario`
  - Test: `pytest tests/integration/test_espejo.py -q`
  - Hecho cuando: un 410 borra el espejo y se hace listado completo sin errores al usuario.

- [x] **TASK-54** · Revisión del Bloque 3 y cobertura medida
  - Objetivo: cerrar el bloque.
  - Trazabilidad: NFR-CAL-01 · Plan: 5.3
  - Diseño: design.md §B3.5
  - Skill: `/agent-skills:review`
  - Archivos: `app/cache.py`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y cobertura dentro del umbral.

## Bloque 4 — Reservas, cola y worker (Fase 6)
*Skills:* `incremental-implementation`, `test-driven-development`, `security-and-hardening`, `debugging-and-error-recovery`

- [x] **TASK-14** · Implementar `reservar` transaccional con bloques UNIQUE
  - Objetivo: reserva + bloques + trabajo CREAR en una sola transacción; idempotente para la misma clienta.
  - Trazabilidad: US-07, EARS-RES-01, EARS-RES-02, EARS-RES-03, EARS-COL-02 · Plan: 6.1
  - Diseño: design.md §2.3, §B4.2, §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/reservas.py`, `app/cola.py`, `tests/integration/test_reservas.py`
  - Entregable: `reservar`, `HorarioOcupado`, `LimiteCitas`, `clave_idempotencia`
  - Test: `pytest tests/integration/test_reservas.py -q`
  - Hecho cuando: un bloque tomado deshace toda la transacción y reconfirmar devuelve la misma reserva.

- [x] **TASK-15** · Probar la concurrencia: la carrera de confirmaciones
  - Objetivo: demostrar que exactamente una gana, incluso con servicios de distinta duración.
  - Trazabilidad: US-07, NFR-INT-01, EC-01, EC-02 · Plan: 6.2
  - Diseño: design.md §B4.5
  - Skill: `/agent-skills:test`
  - Archivos: `tests/integration/test_concurrencia.py`
  - Entregable: tests con hilos (2 y 50 intentos)
  - Test: `pytest tests/integration/test_concurrencia.py -q`
  - Hecho cuando: 0 pares de reservas activas con bloques solapados y la perdedora recibe `HorarioOcupado`.

- [x] **TASK-16** · Implementar la cola: encolar, reclamar FIFO e idempotencia
  - Objetivo: cola en SQLite con reclamo atómico por `BEGIN IMMEDIATE`.
  - Trazabilidad: US-07, EARS-COL-01, EARS-COL-02 · Plan: 6.3
  - Diseño: design.md §1.5, §B4.2
  - Skill: `/agent-skills:test`
  - Archivos: `app/cola.py`, `tests/integration/test_cola.py`
  - Entregable: `encolar`, `reclamar_siguiente`, `completar`, `reintentar`, `fallar`; recuperación de PROCESANDO huérfanos
  - Test: `pytest tests/integration/test_cola.py -q`
  - Hecho cuando: dos reclamos simultáneos nunca devuelven el mismo trabajo y la clave duplicada se rechaza.

- [x] **TASK-17** · Implementar el worker con verificación en vivo y creación del evento
  - Objetivo: un worker por calendario; falla definitiva si hay ocupación; 409 = éxito.
  - Trazabilidad: US-07, EARS-COL-05, EARS-COL-06, EARS-RES-04, EARS-CAL-01, EARS-CAL-02 · Plan: 6.3
  - Diseño: design.md §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/worker.py`, `tests/integration/test_worker.py`
  - Entregable: bucle del worker, ejecución de CREAR, notificación de confirmación/fallo
  - Test: `pytest tests/integration/test_worker.py -q -k crear`
  - Hecho cuando: evento manual en el hueco libera la reserva; el reintento tras caída no duplica.

- [x] **TASK-18** · Añadir backoff con jitter, límite de intentos y pausa por cuota
  - Objetivo: reintentos hasta 5, tope 64 s, pausa de cola ante 403/429 con `usageLimits`.
  - Trazabilidad: US-07, EARS-COL-03, EARS-COL-04, EARS-RES-05, EC-05 · Plan: 6.3
  - Diseño: design.md §1.3, §B4.4
  - Skill: `/agent-skills:test`, `debugging-and-error-recovery`
  - Archivos: `app/worker.py`, `app/cola.py`, `tests/integration/test_worker.py`
  - Entregable: política de reintentos con reloj inyectable
  - Test: `pytest tests/integration/test_worker.py -q -k "backoff or cuota"`
  - Hecho cuando: un 429 simulado reintenta, pausa la cola y termina con un solo evento.

- [x] **TASK-19** · Expirar reservas PENDIENTE a los 10 minutos
  - Objetivo: tarea de limpieza que libera bloques y avisa.
  - Trazabilidad: US-07, EARS-RES-06, EC-08 · Plan: 6.4
  - Diseño: design.md §1.5, §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/reservas.py`, `app/worker.py`, `tests/integration/test_expiracion.py`
  - Entregable: `expirar_pendientes` y tarea periódica cada 60 s (también borra `mensajes_procesados` > 7 días)
  - Test: `pytest tests/integration/test_expiracion.py -q`
  - Hecho cuando: una reserva atascada expira, libera su horario y no expira una PROCESANDO.

- [x] **TASK-20** · Implementar `cancelar` con estado CANCELANDO
  - Objetivo: retener bloques hasta que el evento se elimine.
  - Trazabilidad: US-05, EARS-RES-08, EC-12 · Plan: 6.4
  - Diseño: design.md §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/reservas.py`, `app/worker.py`, `tests/integration/test_cancelar.py`
  - Entregable: `cancelar` y ejecución de ELIMINAR (404/410 = éxito)
  - Test: `pytest tests/integration/test_cancelar.py -q`
  - Hecho cuando: los bloques se liberan solo tras eliminar el evento y la cancelación con CREAR pendiente se encola detrás.

- [x] **TASK-21** · Implementar `reagendar` sin dejar a la clienta sin cita
  - Objetivo: crear la nueva, eliminar la anterior y revertir si algo falla; transferir bloques propios.
  - Trazabilidad: US-04, EARS-RES-07, EC-13 · Plan: 6.4
  - Diseño: design.md §2.3, §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/reservas.py`, `app/worker.py`, `tests/integration/test_reagendar.py`
  - Entregable: `reagendar` y ejecución de REAGENDAR con reversión
  - Test: `pytest tests/integration/test_reagendar.py -q`
  - Hecho cuando: ante fallo al crear o al eliminar, la clienta conserva su cita original.

- [x] **TASK-22** · Consultar citas futuras por teléfono y aplicar el límite por clienta
  - Objetivo: listar citas propias y respetar `max_citas_futuras_por_clienta`.
  - Trazabilidad: US-04, US-05, EARS-RES-09, EARS-CON-07, EC-19 · Plan: 6.5
  - Diseño: design.md §B4.2, §B4.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/reservas.py`, `app/calendario.py` (contrato), `tests/integration/test_citas_futuras.py`
  - Entregable: `citas_futuras` (BD) y aislamiento por teléfono
  - Test: `pytest tests/integration/test_citas_futuras.py -q`
  - Hecho cuando: una clienta nunca ve ni modifica citas de otro teléfono.

- [x] **TASK-55** · Revisión del Bloque 4 con agentes en paralelo y cobertura medida
  - Objetivo: `code-reviewer`, `security-auditor` y `test-engineer` en paralelo; simplificación.
  - Trazabilidad: NFR-CAL-01, NFR-INT-01 · Plan: 6.6
  - Diseño: design.md §B4.5
  - Skill: `/agent-skills:review`, `/agent-skills:code-simplify`
  - Archivos: `app/reservas.py`, `app/cola.py`, `app/worker.py`
  - Entregable: informe en `AI_PROCESS.md`; sin hallazgos críticos ni altos
  - Test: `make test`
  - Hecho cuando: verde y cobertura 100 % de ramas en reservas, cola y worker.

## Bloque 5 — Máquina de estados (Fase 7)
*Skills:* `incremental-implementation`, `test-driven-development`

- [x] **TASK-23** · Implementar estados, transiciones y reinicio por inactividad
  - Objetivo: tabla de transiciones persistida por teléfono.
  - Trazabilidad: US-03, EARS-CON-01, EARS-CON-02 · Plan: 7.1
  - Diseño: design.md §B5.2, §B5.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/conversacion.py`, `tests/unit/test_conversacion.py`
  - Entregable: `Estado`, `Contexto`, `procesar` con transiciones válidas
  - Test: `pytest tests/unit/test_conversacion.py -q -k "transiciones or inactividad"`
  - Hecho cuando: toda transición no permitida se rechaza y 30 min sin actividad reinician a `INICIO`.

- [x] **TASK-24** · Implementar el flujo de agendar con botones y listas
  - Objetivo: servicio → día → hora → confirmar, con ids interactivos y `CONFIRMANDO` solo desde una hora ofrecida.
  - Trazabilidad: US-02, US-03, EARS-CON-03, EARS-CON-05, EARS-RES-04 · Plan: 7.2
  - Diseño: design.md §B5.2, §B5.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/conversacion.py`, `config/mensajes.yaml`, `tests/unit/test_conversacion.py`
  - Entregable: flujo completo hasta `reservar`, respuesta "apartada" y manejo de `HorarioOcupado`
  - Test: `pytest tests/unit/test_conversacion.py -q -k agendar`
  - Hecho cuando: ninguna ruta llega a `CONFIRMANDO` sin selección de hora y el choque reofrece horarios.

- [x] **TASK-25** · Añadir modalidad domicilio con dirección, zona, recargo y total
  - Objetivo: estado de modalidad solo si el domicilio está habilitado y el servicio lo permite.
  - Trazabilidad: US-06, EARS-CON-04, EARS-DOM-01..04, EC-17 · Plan: 7.3
  - Diseño: design.md §B5.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/conversacion.py`, `config/mensajes.yaml`, `tests/unit/test_domicilio.py`
  - Entregable: flujo de domicilio con total (servicio + recargo) antes de confirmar
  - Test: `pytest tests/unit/test_domicilio.py -q`
  - Hecho cuando: con el interruptor apagado el flujo no menciona domicilio y encendido muestra el total.

- [x] **TASK-26** · Implementar los flujos de reagendar y cancelar
  - Objetivo: elegir su cita futura y confirmar la acción.
  - Trazabilidad: US-04, US-05, EARS-RES-07, EARS-RES-08 · Plan: 7.4
  - Diseño: design.md §B5.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/conversacion.py`, `config/mensajes.yaml`, `tests/unit/test_gestion.py`
  - Entregable: estado `GESTIONANDO_CITA` completo
  - Test: `pytest tests/unit/test_gestion.py -q`
  - Hecho cuando: la clienta solo ve y modifica sus propias citas.

- [ ] **TASK-27** · Resolver palabras clave e intenciones fijas y FAQ sin LLM
  - Objetivo: saludos, precio, ubicación, horario, cancelar y FAQ por palabras clave.
  - Trazabilidad: US-01, EARS-INT-01, EARS-CON-08 · Plan: 7.5
  - Diseño: design.md §B5.4, §B7.1
  - Skill: `/agent-skills:test`
  - Archivos: `app/intencion.py`, `config/intenciones.yaml`, `config/faq.yaml`, `tests/unit/test_palabras_clave.py`
  - Entregable: `resolver_por_palabras_clave` y FAQ
  - Test: `pytest tests/unit/test_palabras_clave.py -q`
  - Hecho cuando: "cuánto vale" y "dirección" se resuelven sin invocar al proveedor.

- [ ] **TASK-28** · Añadir aviso de tratamiento de datos, deduplicación de mensajes y mensajes no textuales
  - Objetivo: primer contacto con aviso; descartar `wa_message_id` repetido; menú ante audio/imagen.
  - Trazabilidad: US-13, US-07, EARS-CON-06, EARS-WA-04, EC-03, EC-18 · Plan: 7.5
  - Diseño: design.md §B5.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/conversacion.py`, `tests/unit/test_dedup_aviso.py`
  - Entregable: control de `aviso_datos_aceptado` y `mensajes_procesados`
  - Test: `pytest tests/unit/test_dedup_aviso.py -q`
  - Hecho cuando: el mismo mensaje dos veces produce un solo efecto y no se piden datos antes del aviso.

- [ ] **TASK-29** · Probar E2E el flujo de agendar con el LLM deshabilitado
  - Objetivo: demostrar que el sistema funciona completo con `PROVEEDOR_LLM=none`.
  - Trazabilidad: US-11, EARS-INT-05, NFR-DIS-01 · Plan: 7.6
  - Diseño: design.md §B5.5
  - Skill: `/agent-skills:test`
  - Archivos: `tests/e2e/test_agendar_sin_llm.py`
  - Entregable: test de punta a punta con BD efímera, worker y `CalendarioFalso`
  - Test: `pytest tests/e2e/test_agendar_sin_llm.py -q`
  - Hecho cuando: la cita queda CONFIRMADA con evento en el calendario falso sin ninguna llamada a un LLM.

- [ ] **TASK-56** · Revisión del Bloque 5 y cobertura medida
  - Objetivo: cerrar el bloque.
  - Trazabilidad: NFR-CAL-01 · Plan: 7.7
  - Diseño: design.md §B5.5
  - Skill: `/agent-skills:review`, `/agent-skills:code-simplify`
  - Archivos: `app/conversacion.py`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y cobertura 100 % de ramas en la máquina de estados.

## Bloque 6 — Simulador (Fase 8)
*Skills:* `frontend-ui-engineering`, `browser-testing-with-devtools`, `test-driven-development`

- [ ] **TASK-30** · Crear el transporte simulado y el calendario simulado en vivo
  - Objetivo: mismas reglas de conversación, distinto transporte; cambios del calendario emitidos.
  - Trazabilidad: US-12, EARS-SIM-02, EARS-SIM-03 · Plan: 8.1
  - Diseño: design.md §B6.2, §B6.4
  - Skill: `/agent-skills:build`
  - Archivos: `app/simulador/transporte.py`, `app/simulador/calendario_falso.py`, `tests/unit/test_transporte_simulado.py`
  - Entregable: `TransporteSimulado` (implementa `Mensajeria`) y bus de cambios del calendario
  - Test: `pytest tests/unit/test_transporte_simulado.py -q`
  - Hecho cuando: una conversación simulada invoca `conversacion.procesar` y el calendario emite el cambio.

- [ ] **TASK-31** · Exponer las rutas `/demo` solo con `SIMULADOR=true`
  - Objetivo: API de mensajes, calendario (SSE), carrera y reinicio.
  - Trazabilidad: US-12, EARS-SIM-01, EARS-SIM-05 · Plan: 8.2
  - Diseño: design.md §B6.3, §B6.4
  - Skill: `/agent-skills:build`
  - Archivos: `app/simulador/rutas.py`, `app/main.py`, `tests/e2e/test_simulador.py`
  - Entregable: `APIRouter` condicional y `app/main.py` con `lifespan`
  - Test: `pytest tests/e2e/test_simulador.py -q -k rutas`
  - Hecho cuando: las rutas no existen sin `SIMULADOR=true` y existen con él.

- [ ] **TASK-32** · Construir la interfaz `/demo` en formato vertical presentable
  - Objetivo: chat tipo WhatsApp a la izquierda y calendario en vivo a la derecha, apto para grabar en vertical.
  - Trazabilidad: US-12, EARS-SIM-03 · Plan: 8.3
  - Diseño: design.md §B6.4
  - Skill: `frontend-ui-engineering`, `browser-testing-with-devtools`
  - Archivos: `app/simulador/static/`
  - Entregable: HTML/CSS/JS estático, responsivo, contraste y objetivos táctiles ≥ 44 px
  - Test: `pytest tests/e2e/test_simulador.py -q -k ui` más captura de pantalla vertical revisada
  - Hecho cuando: el flujo de agendar se completa desde la UI y el calendario se actualiza solo.

- [ ] **TASK-33** · Añadir el botón de dos confirmaciones simultáneas
  - Objetivo: mostrar visualmente que el sistema impide la cita doble.
  - Trazabilidad: US-12, EARS-SIM-04 · Plan: 8.4
  - Diseño: design.md §B6.4
  - Skill: `/agent-skills:build`
  - Archivos: `app/simulador/rutas.py`, `app/simulador/static/`, `tests/e2e/test_simulador.py`
  - Entregable: endpoint `POST /demo/api/carrera` y botón
  - Test: `pytest tests/e2e/test_simulador.py -q -k carrera`
  - Hecho cuando: la UI muestra una cita confirmada y un rechazo "horario ocupado".

- [ ] **TASK-34** · Preparar la configuración de despliegue de la demo (sin desplegar)
  - Objetivo: `render.yaml` con `SIMULADOR=true`, `PROVEEDOR_LLM=none`, una instancia y disco persistente.
  - Trazabilidad: US-12, NFR-DES-01 · Plan: 8.5
  - Diseño: design.md §5
  - Skill: `ci-cd-and-automation`
  - Archivos: `render.yaml`, `Dockerfile`
  - Entregable: archivo de despliegue; ⚠️ planes y precios de Render verificados en documentación
  - Test: `docker compose build`
  - Hecho cuando: la imagen arranca con `SIMULADOR=true` y `/demo` responde localmente.

- [ ] **TASK-57** · Revisión del Bloque 6
  - Objetivo: cerrar el bloque con revisión y prueba visual.
  - Trazabilidad: NFR-CAL-01 · Plan: 8.6
  - Diseño: design.md §B6.5
  - Skill: `/agent-skills:review`, `browser-testing-with-devtools`
  - Archivos: `app/simulador/`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y la demo se ve correctamente en un viewport vertical.

## Bloque 7 — Capa de intención (Fase 9)
*Skills:* `arquitecto-de-prompts`, `source-driven-development`, `security-and-hardening`, `test-driven-development`

- [ ] **TASK-35** · Definir el esquema Pydantic estricto de la intención
  - Objetivo: validar intención, servicio, fecha, franja y confianza; invalidar la respuesta completa si algo no cuadra.
  - Trazabilidad: US-11, EARS-INT-03, EC-09 · Plan: 9.1
  - Diseño: design.md §B7.2, §B7.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/intencion.py`, `tests/unit/test_intencion_esquema.py`
  - Entregable: modelo `Intencion` con validación de catálogo
  - Test: `pytest tests/unit/test_intencion_esquema.py -q`
  - Hecho cuando: campo extra, fecha imposible o servicio inexistente son rechazados.

- [ ] **TASK-36** · Definir el puerto `ProveedorLLM` y el proveedor `none`
  - Objetivo: proveedor configurable por entorno; `none` siempre devuelve `None`.
  - Trazabilidad: US-11, EARS-INT-05 · Plan: 9.2
  - Diseño: design.md §B7.2
  - Skill: `api-and-interface-design`
  - Archivos: `app/llm/base.py`, `app/llm/ninguno.py`, `app/llm/__init__.py`, `tests/unit/test_llm_ninguno.py`
  - Entregable: `Protocol ProveedorLLM`, fábrica `crear_proveedor(settings)`
  - Test: `pytest tests/unit/test_llm_ninguno.py -q`
  - Hecho cuando: `PROVEEDOR_LLM=none` devuelve el proveedor nulo y el sistema sigue funcionando.

- [ ] **TASK-37** · Implementar el adaptador de Gemini con timeout de 3 s y sin streaming
  - Objetivo: llamada a Flash-Lite con salida estructurada validada localmente.
  - Trazabilidad: US-11, EARS-INT-02, EARS-INT-04, NFR-REN-03 · Plan: 9.3
  - Diseño: design.md §B7.4 (⚠️ verificar documentación de `google-genai`)
  - Skill: `source-driven-development`, `/agent-skills:test`
  - Archivos: `app/llm/gemini.py`, `config/prompt_intencion.txt`, `tests/unit/test_llm_gemini.py`
  - Entregable: adaptador con cliente mockeado; nombre de modelo verificado en documentación oficial y registrado en `AI_PROCESS.md`
  - Test: `pytest tests/unit/test_llm_gemini.py -q`
  - Hecho cuando: timeout, 429 y JSON inválido devuelven `None` sin propagar errores.

- [ ] **TASK-38** · Orquestar palabras clave → LLM → fallback y medir el porcentaje sin LLM
  - Objetivo: orden determinista, umbral de confianza 0.7 y métricas.
  - Trazabilidad: US-01, US-10, EARS-INT-01, EARS-INT-04, EARS-INT-06, EARS-INT-07 · Plan: 9.4
  - Diseño: design.md §B7.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/intencion.py`, `app/metricas.py`, `tests/unit/test_intencion_orquestador.py`
  - Entregable: `interpretar(texto, ctx)` que nunca lanza y nunca produce `CONFIRMANDO`
  - Test: `pytest tests/unit/test_intencion_orquestador.py -q`
  - Hecho cuando: confianza 0.69 cae al menú, 0.70 pasa, y el % sin LLM queda registrado.

- [ ] **TASK-39** · Probar que el prompt no contiene datos personales
  - Objetivo: test de contrato sobre `construir_prompt`.
  - Trazabilidad: US-13, EARS-INT-08, NFR-PRI-01 · Plan: 9.5
  - Diseño: design.md §B7.4
  - Skill: `security-and-hardening`, `/agent-skills:test`
  - Archivos: `app/intencion.py`, `tests/unit/test_prompt_privacidad.py`
  - Entregable: `construir_prompt(texto, catalogo, hoy, estado)` con firma que impide recibir teléfono o nombre
  - Test: `pytest tests/unit/test_prompt_privacidad.py -q`
  - Hecho cuando: ningún teléfono, nombre, dirección ni historial aparece en el prompt en ningún escenario probado.

- [ ] **TASK-40** · Crear el set de evals de frases de ejemplo
  - Objetivo: medir el comportamiento esperado del orquestador con frases reales.
  - Trazabilidad: US-11, EARS-INT-03 · Plan: 9.6
  - Diseño: design.md §B7.1, §B7.5
  - Skill: `arquitecto-de-prompts`
  - Archivos: `tests/evals/frases.yaml`, `tests/evals/test_evals.py`
  - Entregable: ≥ 30 frases con intención esperada (palabras clave y LLM simulado)
  - Test: `pytest tests/evals -q`
  - Hecho cuando: las frases resueltas por palabras clave dan la intención esperada y las ambiguas caen al menú.

- [ ] **TASK-58** · Revisión del Bloque 7
  - Objetivo: cerrar el bloque con revisión de seguridad (prompt injection y fuga de datos).
  - Trazabilidad: NFR-CAL-01, NFR-PRI-01 · Plan: 9.7
  - Diseño: design.md §B7.5
  - Skill: `/agent-skills:review`, agente `security-auditor`
  - Archivos: `app/intencion.py`, `app/llm/`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y sin hallazgos críticos ni altos.

## Bloque 8 — Integración WhatsApp y observabilidad (Fase 10)
*Skills:* `source-driven-development`, `security-and-hardening`, `observability-and-instrumentation`

- [ ] **TASK-41** · Implementar la verificación GET y la firma HMAC del POST
  - Objetivo: `hub.challenge` y 403 si la firma no coincide.
  - Trazabilidad: US-13, EARS-WA-01, EARS-WA-02 · Plan: 10.1
  - Diseño: design.md §B8.3, §B8.4 (⚠️ documentación de Meta)
  - Skill: `security-and-hardening`, `/agent-skills:test`
  - Archivos: `app/webhook.py`, `tests/integration/test_webhook.py`
  - Entregable: verificación sobre cuerpo crudo con `hmac.compare_digest`
  - Test: `pytest tests/integration/test_webhook.py -q -k "verificacion or firma"`
  - Hecho cuando: firma inválida o ausente da 403 y el GET responde el challenge solo con token correcto.

- [ ] **TASK-42** · Procesar el POST con 200 inmediato, background y deduplicación
  - Objetivo: responder rápido y procesar sin propagar errores a Meta.
  - Trazabilidad: US-03, US-07, EARS-WA-03, EARS-WA-04, NFR-REN-01 · Plan: 10.2
  - Diseño: design.md §B8.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/webhook.py`, `tests/integration/test_webhook.py`
  - Entregable: parseo del payload a `Entrante`, `BackgroundTasks`
  - Test: `pytest tests/integration/test_webhook.py -q -k "post or dedup"`
  - Hecho cuando: con procesamiento lento el 200 llega en < 1 s y el mismo `id` produce un solo efecto.

- [ ] **TASK-43** · Implementar el cliente de Graph API con reintentos
  - Objetivo: enviar texto, botones y listas; backoff en 429/5xx.
  - Trazabilidad: US-03, EARS-WA-05, EARS-CON-05 · Plan: 10.3
  - Diseño: design.md §B8.2, §B8.4 (⚠️ límites y payloads verificados en documentación)
  - Skill: `source-driven-development`, `/agent-skills:test`
  - Archivos: `app/whatsapp.py`, `tests/integration/test_whatsapp_cliente.py`
  - Entregable: adaptador de `Mensajeria` con httpx mockeado
  - Test: `pytest tests/integration/test_whatsapp_cliente.py -q`
  - Hecho cuando: respeta el máximo de 3 botones y 10 filas y reintenta sin duplicar envíos.

- [ ] **TASK-44** · Exponer `/health` y logging JSON con teléfono enmascarado
  - Objetivo: métricas operativas sin PII y logs estructurados.
  - Trazabilidad: US-10, EARS-OBS-01, NFR-PRI-02, NFR-OBS-01 · Plan: 10.4
  - Diseño: design.md §1.6, §B8.3, §B8.4
  - Skill: `observability-and-instrumentation`
  - Archivos: `app/main.py`, `app/metricas.py`, `tests/unit/test_logging.py`, `tests/integration/test_health.py`
  - Entregable: `/health`, formateador JSON, `enmascarar()`, filtro de tokens
  - Test: `pytest tests/unit/test_logging.py tests/integration/test_health.py -q`
  - Hecho cuando: ningún log contiene teléfonos completos ni tokens y `/health` expone las cuatro métricas.

- [ ] **TASK-59** · Revisión del Bloque 8
  - Objetivo: cerrar el bloque con revisión de seguridad del webhook.
  - Trazabilidad: NFR-CAL-01, NFR-SEG-01 · Plan: 10.5
  - Diseño: design.md §B8.5
  - Skill: `/agent-skills:review`, agente `security-auditor`
  - Archivos: `app/webhook.py`, `app/whatsapp.py`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde, cobertura 100 % de ramas en la verificación de firma y sin hallazgos altos.

## Bloque 9 — Calendar real y push (Fase 11)
*Skills:* `source-driven-development`, `security-and-hardening`, `test-driven-development`

- [ ] **TASK-45** · Implementar el adaptador de Google Calendar con service account
  - Objetivo: `CalendarioGoogle` con `extendedProperties`, `location` y mapeo de errores.
  - Trazabilidad: US-08, US-06, EARS-CAL-01, EARS-CAL-02, EARS-COL-03 · Plan: 11.1
  - Diseño: design.md §B9.2 (⚠️ documentación oficial de Calendar v3)
  - Skill: `source-driven-development`, `/agent-skills:test`
  - Archivos: `app/calendario.py`, `tests/integration/test_calendario_google.py`
  - Entregable: adaptador del puerto `Calendario` con cliente HTTP simulado en los tests
  - Test: `pytest tests/integration/test_calendario_google.py -q`
  - Hecho cuando: 403/429 con `usageLimits` se mapean a `CuotaExcedida` y 5xx a `ErrorTransitorio`.

- [ ] **TASK-46** · Implementar `events.watch`, `/webhook/calendar` y la renovación del canal
  - Objetivo: invalidar el caché al instante cuando la dueña agenda a mano.
  - Trazabilidad: US-08, EARS-CAC-02, EARS-CAC-03 · Plan: 11.2
  - Diseño: design.md §B9.3, §B9.4
  - Skill: `source-driven-development`, `/agent-skills:test`
  - Archivos: `app/calendario.py`, `app/webhook.py`, `tests/integration/test_push.py`
  - Entregable: registro del canal, endpoint con token, tarea de renovación antes de expirar
  - Test: `pytest tests/integration/test_push.py -q`
  - Hecho cuando: push válido invalida el caché, inválido da 403 y el canal se renueva antes de caducar.

- [ ] **TASK-47** · Implementar el modo degradado con refresco cada 5 minutos
  - Objetivo: seguir funcionando sin HTTPS público y registrarlo.
  - Trazabilidad: US-08, EARS-CAC-04 · Plan: 11.3
  - Diseño: design.md §1.5, §B9.4
  - Skill: `/agent-skills:test`
  - Archivos: `app/cache.py`, `app/main.py`, `tests/integration/test_modo_degradado.py`
  - Entregable: tarea de refresco, `modo_degradado` en `calendario_estado` y en `/health`
  - Test: `pytest tests/integration/test_modo_degradado.py -q`
  - Hecho cuando: sin canal válido el refresco corre cada 5 min y queda el log `modo_degradado`.

- [ ] **TASK-60** · Revisión del Bloque 9
  - Objetivo: cerrar el bloque.
  - Trazabilidad: NFR-CAL-01 · Plan: 11.4
  - Diseño: design.md §B9.5
  - Skill: `/agent-skills:review`, agente `security-auditor`
  - Archivos: `app/calendario.py`
  - Entregable: informe en `AI_PROCESS.md`
  - Test: `make test`
  - Hecho cuando: verde y sin hallazgos críticos ni altos.

## Bloque 10 — Documentación (Fase 12)
*Skills:* `documentation-and-adrs`

- [ ] **TASK-48** · Redactar el README técnico con el diagrama Mermaid del flujo completo
  - Objetivo: instalación, despliegue, variables, operación de la cola, privacidad y `GEMINI_TIER`.
  - Trazabilidad: NFR-DES-01, EARS-INT-02 · Plan: 12.1
  - Diseño: design.md §B10.4 (⚠️ precios y política de datos de Gemini verificados al redactar)
  - Skill: `documentation-and-adrs`
  - Archivos: `README.md`
  - Entregable: parte técnica con estimación de costo para 300 conversaciones y recordatorios como extensión futura
  - Test: `make lint`
  - Hecho cuando: el diagrama renderiza y cada variable de `.env.example` está documentada.

- [ ] **TASK-49** · Redactar el manual para la clienta (dueña) sin tecnicismos
  - Objetivo: crear WhatsApp Business, compartir el calendario, cambiar precios y horarios, activar el domicilio y su recargo.
  - Trazabilidad: US-09, EARS-DOM-05 · Plan: 12.2
  - Diseño: design.md §B10.4
  - Skill: `documentation-and-adrs`
  - Archivos: `README.md`, `docs/manual-clienta.md`
  - Entregable: parte "PARA LA CLIENTA" separada de la técnica
  - Test: `make lint`
  - Hecho cuando: una persona no técnica puede seguir los pasos sin conocimientos de programación.

- [ ] **TASK-50** · Redactar runbook y CHANGELOG
  - Objetivo: operación de la cola, restauración de SQLite, canal expirado, cuota agotada.
  - Trazabilidad: NFR-DES-01 · Plan: 12.3
  - Diseño: design.md §B10.1
  - Skill: `documentation-and-adrs`
  - Archivos: `docs/runbook.md`, `CHANGELOG.md`
  - Entregable: runbook y CHANGELOG
  - Test: `make lint`
  - Hecho cuando: existe procedimiento para cada alerta del log y para el rollback.

## Bloque 11 — Lanzamiento (Fase 13)
*Skills:* `shipping-and-launch`, `ci-cd-and-automation`

- [ ] **TASK-51** · Ejecutar el checklist de lanzamiento y decidir go/no-go
  - Objetivo: revisión final en paralelo de las condiciones diferidas (PA-07, PA-09, PA-10, PA-15).
  - Trazabilidad: NFR-CAL-01, NFR-SEG-01 · Plan: 13.1
  - Diseño: SDD §17
  - Skill: `/agent-skills:ship`
  - Archivos: `docs/`, `CHANGELOG.md`
  - Entregable: informe go/no-go
  - Test: `make ci`
  - Hecho cuando: el usuario da el go explícito y las condiciones diferidas están resueltas o aceptadas.

- [ ] **TASK-61** · Desplegar la demo en Render (solo con confirmación del usuario)
  - Objetivo: demo pública del simulador para el cliente.
  - Trazabilidad: US-12, NFR-DES-01 · Plan: 13.2
  - Diseño: design.md §5
  - Skill: `ci-cd-and-automation`
  - Archivos: `render.yaml`
  - Entregable: URL de la demo
  - Test: `curl -fsS <URL>/health`
  - Hecho cuando: `/demo` carga y la carrera muestra una ganadora y un rechazo; el usuario confirmó plan y costo antes de crear el servicio.

- [ ] **TASK-62** · Crear el release `vX.Y.Z` a `main` (solo con confirmación del usuario)
  - Objetivo: `release/vX.Y.Z` desde `develop` y tag.
  - Trazabilidad: SDD §17 · Plan: 13.3
  - Diseño: AGENTS.md §6
  - Skill: `git-workflow-and-versioning`
  - Archivos: raíz del proyecto
  - Entregable: tag y merge a `main` y `develop`
  - Test: `git tag --list`
  - Hecho cuando: el usuario confirmó el merge y el tag existe.
