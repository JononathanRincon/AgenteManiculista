# Modelo de amenazas (STRIDE) — AgenteManiculista

> Ampliación de `SDD.md` §11.3. Versión del SDD: 0.3. Se actualiza si en la fase 5 de una revisión aparece una amenaza nueva.

## Activos
Datos personales de clientas (teléfono, nombre de perfil, dirección de domicilio), calendario de la dueña, integridad de la agenda (sin citas dobles), secretos (token de WhatsApp, app secret, service account, API key de Gemini).

## Fronteras de confianza
1. Sidecar → `/webhook/whatsapp` (v0.4, red interna, HMAC) e Internet → `/webhook/calendar` (Google): origen verificado por firma y token de canal.
2. Aplicación → LLM (tercero): solo texto, catálogo, fecha y nombre de estado.
3. Aplicación → Google Calendar y Graph API: credenciales por entorno.
4. `/demo/*`: solo existe en modo simulador.

## Amenazas y mitigaciones
| # | Flujo / activo | Amenaza | STRIDE | Mitigación | Requisito | Prueba |
|---|---|---|---|---|---|---|
| T1 | `POST /webhook/whatsapp` | Petición falsa que simula al sidecar | Spoofing | HMAC SHA-256 sobre cuerpo crudo, `hmac.compare_digest`, 403 | EARS-WA-02 | TASK-68 |
| T15 | Sesión de Baileys | Robo de `/data/baileys_auth` = control de la línea | Information disclosure / Elevation | Volumen restringido y no versionado, QR con token, rotación | EARS-WA-11, -14 | TASK-70, 71 |
| T16 | Línea de WhatsApp | Bloqueo por detección de automatización | DoS | Humanización, solo tráfico entrante, debounce, handoff, rate limit | EARS-WA-06..18 | TASK-64..67 |
| T2 | Mensajes | Reenvío del mismo mensaje | Tampering | Dedup por `wa_message_id`; clave de idempotencia; `id` de evento derivado | EARS-WA-04, EARS-COL-02 | TASK-42, 14 |
| T3 | Reserva | Cita doble por carrera | Tampering | UNIQUE por bloque + `BEGIN IMMEDIATE` + worker único + verificación en vivo | EARS-RES-01, EARS-COL-05 | TASK-15 |
| T4 | Reagendar/cancelar | Suplantar a otra clienta | Spoofing / Elevation | Identidad = teléfono firmado por Meta; consultas filtradas por teléfono | EARS-RES-09 | TASK-22 |
| T5 | LLM | Prompt injection ("confirma mi cita") | Tampering / Elevation | LLM sin herramientas; salida validada; no llega a `CONFIRMANDO`; confirmar solo por botón | EARS-INT-03, 07 | TASK-35, 38 |
| T6 | LLM | Fuga de PII a un tercero | Information disclosure | `construir_prompt` sin parámetros de identidad; test de contrato; `GEMINI_TIER` de pago en producción | EARS-INT-08 | TASK-39 |
| T7 | Logs | Teléfonos completos o tokens | Information disclosure | Enmascarado `***1234`; filtro de tokens; sin contenido al LLM | NFR-PRI-02 | TASK-44 |
| T8 | `/webhook/calendar` | Notificaciones falsas para vaciar el caché y forzar llamadas | DoS | Token de canal; límite de tasa; el caché solo se invalida, no se escribe | EARS-CAC-02 | TASK-46 |
| T9 | Cola / Calendar | Saturación o cuota agotada | DoS | Backoff con jitter, pausa por calendario, un worker | EARS-COL-03, 04 | TASK-18 |
| T10 | `/demo` en producción | Endpoints de demo expuestos | Elevation | Rutas no registradas si `SIMULADOR` ≠ true | EARS-SIM-01 | TASK-31 |
| T11 | SQLite | Pérdida o copia del archivo | Information disclosure / DoS | Disco persistente restringido, backups cifrados, retención mínima | SDD §6.5 | runbook |
| T12 | Service account | Clave filtrada | Elevation | Variable de entorno, permiso sobre un solo calendario, rotación | NFR-SEG-02 | TASK-02 (escaneo) |
| T13 | Dirección de domicilio | Exposición en el evento a terceros | Information disclosure | El calendario es privado de la dueña; la dirección se borra de la BD al cerrar la cita | SDD §6.5 | TASK-25 |
| T14 | Repudio de acciones | La clienta niega haber cancelado | Repudiation | Registro de cambios de estado de la reserva con marca de tiempo y teléfono enmascarado | NFR-OBS-01 | TASK-44 |

## Riesgo residual aceptado
Evento manual de la dueña creado en los milisegundos entre la verificación en vivo y el `events.insert`: Calendar no impide solapes. Ventana de milisegundos; la dueña lo ve en su celular.
