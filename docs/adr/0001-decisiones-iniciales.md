# ADR — Decisiones iniciales (SDD v0.3)

Estado de todas: **Aceptada** con la aprobación del SDD v0.3 (2026-10-30). Fuente: SDD §19.

## ADR-001 — El LLM interpreta, nunca ejecuta
**Contexto:** un LLM que confirma citas alucina horarios. **Decisión:** el LLM solo produce JSON validado; disponibilidad, escritura y confirmación son código determinista. **Consecuencias:** el fallback por botones es el camino principal; hay que mantener el esquema estricto.

## ADR-002 — Anti-cita-doble por bloques
**Contexto:** `UNIQUE(calendario_id, inicio)` no impide solapes de distinta duración. **Decisión:** tabla `reserva_bloques` con `PRIMARY KEY (calendario_id, bloque_inicio)` y una fila por bloque ocupado (duración + margen + desplazamiento), insertada en la misma transacción que la reserva; más escritor único y verificación en vivo. **Alternativas:** constraint de exclusión (no existe en SQLite). **Consecuencias:** coste de unas pocas filas por reserva.

## ADR-003 — Cola en SQLite con `BEGIN IMMEDIATE`
**Contexto:** una instancia pequeña; sin Redis ni Celery. **Decisión:** `cola_escritura` en SQLite (WAL), un worker por calendario, `BEGIN IMMEDIATE` en lugar de `SELECT … FOR UPDATE`. **Consecuencias:** un solo proceso (`--workers 1`) y una sola instancia.

## ADR-004 — Idempotencia en tres niveles
**Decisión:** `wa_message_id` PK; `clave_idempotencia = hash(telefono, inicio, tipo, reserva_id)`; `id` de evento de Calendar derivado de `reserva_id` (409 = éxito). **Motivo:** incluir `reserva_id` evita rechazar cancelar y volver a agendar el mismo horario.

## ADR-005 — Caché con espejo `events.list` + `syncToken`; push para invalidar
**Decisión:** los sync tokens pertenecen a `events.list`; el espejo local alimenta el caché; la verificación final siempre es en vivo en el worker. **Consecuencias:** modo degradado de 5 min sin HTTPS público.

## ADR-006 — Domicilio como interruptor de configuración
**Decisión:** `domicilio.habilitado` en `negocio.yaml`; encendido exige `recargo_cop` ≥ 0; el desplazamiento suma bloques. Apagado, el flujo no lo menciona.

## ADR-007 — Demo y producción en Render; Vercel descartado
**Contexto:** Vercel ejecuta funciones efímeras con disco de solo lectura y varias instancias. **Decisión:** un servicio Docker con disco persistente y una instancia en Render; la demo corre con `SIMULADOR=true` y `PROVEEDOR_LLM=none`. **Pendiente:** verificar planes y precios antes de desplegar (con confirmación del usuario).

## ADR-008 — Estado CANCELANDO que retiene bloques
**Decisión:** cancelar pasa a CANCELANDO y conserva los bloques hasta que el worker elimina el evento, para que otra clienta no tome un horario cuyo evento aún existe en Calendar.

## ADR-009 (pendiente) — Migraciones de esquema
Sin Alembic en el MVP (`create_all`). Se decide una herramienta cuando exista un dato real en producción, con `deprecation-and-migration` (expand/contract).
