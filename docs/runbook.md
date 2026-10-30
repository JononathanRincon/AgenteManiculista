# Runbook de operación

Para quien administra el sistema. Todos los comandos asumen `docker compose` en la carpeta del
proyecto. El estado general se ve en `GET /health`. Los logs son JSON con el teléfono enmascarado.

## Alertas del log y qué hacer

| Evento del log | Nivel | Qué significa | Procedimiento |
|---|---|---|---|
| `whatsapp_sesion_cerrada` | CRITICAL | La dueña cerró la sesión o WhatsApp la invalidó (`loggedOut`) | [Volver a vincular](#whatsapp-sesión-cerrada-o-número-restringido) |
| `sidecar_sin_respuesta` | ERROR | El backend no alcanza al sidecar | Ver `docker compose --profile whatsapp ps` y los logs del sidecar; reiniciarlo con `docker compose restart sidecar` |
| `trabajo_fallido` | ERROR | Un trabajo de la cola agotó sus 5 intentos | [Cola](#cola-y-trabajos-fallidos) |
| `trabajo_error_inesperado` | ERROR | Excepción no prevista procesando un trabajo | Revisar el log completo; reportar el defecto con el id del trabajo |
| `cancelacion_fallida` | ERROR | No se pudo borrar el evento de una cita cancelada | La reserva vuelve a CONFIRMADA; revisar Calendar y reintentar la cancelación |
| `cola_pausada` | WARNING | Cuota de Calendar agotada (403/429 `usageLimits`) | [Cuota](#cuota-de-calendar-agotada) |
| `modo_degradado` | WARNING | No hay canal push válido; se refresca cada 5 min | [Canal push](#canal-push-expirado-o-en-modo-degradado) |
| `canal_no_registrado` | WARNING | Google rechazó o no pudo registrar el canal | Igual que arriba; revisar `CALENDAR_WEBHOOK_URL` |
| `canal_anterior_sin_detener` | WARNING | No se pudo detener el canal viejo tras renovar | Sin acción: caduca solo (hasta 7 días de avisos duplicados) |
| `mantenimiento_calendario_fallido` | ERROR | Un ciclo de renovación/refresco falló | Si se repite, revisar credenciales y red; la tarea sigue viva |
| `resincronizacion_fallida` | WARNING | Falló el refresco tras un aviso push | El espejo se refresca en la siguiente consulta; sin acción si no se repite |
| `compensacion_fallida` | WARNING | Falló deshacer un paso al reagendar | Revisar la reserva afectada contra Calendar |
| `confirmacion_rechazada` | WARNING | La hora ya no estaba libre al confirmar | Normal en una carrera; la clienta recibe otra oferta |
| `envio_descartado` | INFO/WARNING | Mensaje no enviado por pausa humana, límite de tasa o sin conversación | Normal; si es masivo, revisar `anti_ban` |
| `gemini_circuito_abierto` | ERROR | Gemini falló repetidamente; se usa solo botones y palabras clave | El sistema sigue funcionando; revisar clave, cuota y estado de Gemini |
| `gemini_error`, `gemini_error_api`, `llm_fallo_inesperado` | WARNING | Fallo puntual del LLM | Sin acción si no se repite |
| `llm_cupo_agotado` | WARNING | Tope por clienta/hora o global/día alcanzado | Esperar o subir `llm_max_*` en `config/negocio.yaml` |

## WhatsApp: sesión cerrada o número restringido

El sidecar guarda la sesión en el volumen `baileys_auth`. Terminal (no se reconecta solo): `loggedOut`
(401), `connectionReplaced` (440) y `forbidden` (403). `/health` lo muestra en `whatsapp_estado`.

1. **`loggedOut`:** reinicia el sidecar y pide el QR con
   `curl -H "X-Admin-Token: $ADMIN_TOKEN" http://<host-interno>/admin/whatsapp/qr` (la ruta solo
   existe con `ADMIN_TOKEN`; no la expongas a Internet). La dueña lo escanea (Dispositivos vinculados).
   Si la sesión no vuelve, borra el volumen `baileys_auth` y repite.
2. **440 (sesión reemplazada):** hay otra instancia usando la misma sesión; apaga la otra.
3. **403 (prohibida):** WhatsApp restringió el número. **Detén el sidecar** (`docker compose stop
   sidecar`), no insistas con reconexiones, avisa a la dueña y evalúa el plan B (WhatsApp Cloud API,
   ADR-009). Reintentar acelera la sanción.
4. Tras 10 fallos seguidos de conexión el sidecar espera 15 min entre intentos: es intencional.

## Cola y trabajos fallidos

- Ver: `GET /health` → `cola_profundidad`, `trabajos_fallidos`, `cola_pausada_hasta`.
- Un trabajo falla tras 5 intentos (backoff 1-64 s). Una CREAR fallida deja la reserva en FALLIDA y
  avisa a la clienta; una ELIMINAR fallida devuelve la reserva a CONFIRMADA.
- Orden estricto FIFO y un solo worker: no levantes varias instancias (`--workers 1`).
- No borres filas a mano; abre un caso con el id del trabajo.

## Cuota de Calendar agotada

El worker pausa la cola (`cola_pausada`) y la reanuda sola cuando pasa la espera. No hay pérdida de
reservas: quedan PENDIENTES (expiran a los `reserva_pendiente_expira_minutos`). Si ocurre a diario,
revisa en Google Cloud la cuota de Calendar API y que no haya otro proceso usando la cuenta de servicio.

## Canal push expirado o en modo degradado

Los canales `events.watch` duran hasta 7 días; el sistema crea uno nuevo 24 h antes de que venza
y detiene el anterior. Si `/health` muestra `modo_degradado: true`:
1. Verifica que `CALENDAR_WEBHOOK_URL` sea **HTTPS público**, termine en `/webhook/calendar` y llegue
   al backend (el proxy debe exponer solo esa ruta).
2. Verifica `CALENDAR_CHANNEL_TOKEN` (≥ 32 caracteres) y los logs `canal_no_registrado`.
3. Mientras tanto el sistema funciona: refresca cada 5 min y verifica Calendar en vivo antes de escribir.

- **Rotar `CALENDAR_CHANNEL_TOKEN`:** cambia la variable y fuerza la renovación del canal, porque
  Google sigue enviando el token anterior hasta que expira: pon `canal_expira_en` en `NULL` en la tabla
  `calendario_estado` de `/data/agenda.db` y reinicia el backend.
- **Base de datos perdida:** los canales registrados quedan huérfanos hasta 7 días (avisos
  duplicados inofensivos). Por eso `/data` debe ser un volumen persistente.
- **Calendario muy grande o con recurrencias infinitas:** el listado completo puede agotar el tope de
  páginas y fallar el refresco. Síntoma: `mantenimiento_calendario_fallido` repetido. Acción: archivar
  eventos antiguos o quitar recurrencias sin fin.

## Respaldo y restauración de SQLite

`/data/agenda.db` está en modo WAL. **Respaldo en caliente** con la API de respaldo de SQLite
(`sqlite3.Connection.backup`), que da una copia consistente aunque el servicio esté escribiendo.
Guarda el respaldo fuera del servidor y **cifrado**: contiene teléfonos de clientas (Ley 1581).

**Restaurar:** `docker compose stop backend`, reemplaza `agenda.db` por el respaldo (y borra
`agenda.db-wal` y `agenda.db-shm`), `docker compose up -d`. Las reservas PENDIENTES expiran solas;
compara con Calendar si el respaldo es viejo. Haz lo mismo, cifrado, con el volumen `baileys_auth`:
equivale a tener la sesión de WhatsApp.

## Rotación de secretos

Genera valores nuevos (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) para
`SIDECAR_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_TOKEN` y `CALENDAR_CHANNEL_TOKEN`; actualiza el entorno del
backend **y** del sidecar (`SIDECAR_TOKEN`, `WEBHOOK_SECRET`) y reinicia ambos a la vez. Clave de la
cuenta de servicio: crea una nueva en Google Cloud, despliega y revoca la anterior.

## Despliegue y rollback

- Antes de cada despliegue: `make ci`, `docker compose build` y respaldo de `/data`.
- Despliegue: `docker compose up -d` (un solo contenedor de backend). Verifica `GET /health`.
- **Rollback:** vuelve a la imagen anterior (`docker compose up -d` con la imagen previa) y, si hubo
  cambio de esquema, restaura el respaldo previo. Prueba el rollback antes de producción.
- Push, merges a `main`, tags y despliegues requieren confirmación explícita del responsable.
