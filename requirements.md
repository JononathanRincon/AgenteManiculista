# requirements.md — Especificación Conceptual y Requisitos EARS
## AgenteManiculista

> **Documento derivado del `SDD.md` aprobado** (§1, §2, §6.5, §9, §10, §11.1, §15, §16).
> No agrega requisitos nuevos: si falta algo, se actualiza primero el SDD y el usuario lo re-aprueba.
> **Solo lectura para el agente.** En el loop se leen únicamente las US, EARS y NFR que cita la tarea.

Versión del SDD de origen: 0.3 · Fecha: 2026-10-30

---

## 1. Contexto y modelo de negocio
Una manicurista independiente en Colombia agenda a mano por WhatsApp mientras atiende. El dolor es la
**cita doble**. El sistema permite a las clientas consultar, agendar, reagendar y cancelar por WhatsApp sin
intervención humana, viendo solo horarios reales; las citas viven en el Google Calendar de la dueña. El LLM
solo interpreta lenguaje natural; el código determinista decide y escribe. Meta: 0 citas dobles atribuibles
al sistema; ≥ 80 % de conversaciones de agenda sin intervención (propuesta).

## 2. Roles
| Rol | Descripción |
|---|---|
| Clienta | Escribe por WhatsApp; solo ve y gestiona sus propias citas |
| Dueña | Titular del calendario y de la configuración YAML |
| Operador | Despliega, opera y monitorea (`/health`, logs) |
| Sistema (worker) | Única identidad que escribe en Calendar |

## 3. Historias de usuario
### 3.1 Clienta
- **US-01:** *Como* clienta, *quiero* consultar servicios, precios, horario y ubicación por WhatsApp, *para* decidir sin esperar respuesta humana.
- **US-02:** *Como* clienta, *quiero* ver solo horarios realmente libres, *para* no quedar agendada en uno ocupado.
- **US-03:** *Como* clienta, *quiero* agendar una cita escribiendo en lenguaje natural o tocando botones, *para* hacerlo rápido.
- **US-04:** *Como* clienta, *quiero* reagendar mi cita sin perderla si algo falla, *para* no quedar sin turno.
- **US-05:** *Como* clienta, *quiero* cancelar mi cita, *para* liberar el espacio.
- **US-06:** *Como* clienta, *quiero* elegir atención en local o a domicilio cuando la dueña lo ofrezca, *para* la modalidad que me convenga.
- **US-13:** *Como* clienta, *quiero* que mis datos personales se traten con mi autorización, *para* proteger mi privacidad.
### 3.2 Dueña
- **US-07:** *Como* dueña, *quiero* que nunca haya dos citas a la misma hora, *para* dejar de tener cruces.
- **US-08:** *Como* dueña, *quiero* ver las citas en mi Google Calendar del celular y poder agendar a mano, *para* no instalar nada nuevo.
- **US-09:** *Como* dueña, *quiero* cambiar precios, horarios y activar el domicilio editando archivos sencillos, *para* no depender del desarrollador.
### 3.3 Operador
- **US-10:** *Como* operador, *quiero* ver métricas en `/health`, *para* detectar cola atascada o fallos.
- **US-11:** *Como* operador, *quiero* ejecutar todo sin LLM, *para* garantizar el servicio si el proveedor cae.
- **US-12:** *Como* operador, *quiero* un simulador con calendario en vivo y una prueba de carrera, *para* demostrar al cliente antes de tener credenciales de Meta.

## 4. Requisitos funcionales (EARS)
Formato: `EARS-<DOMINIO>-NN (Tipo)`. Tipos: Ubicuo, Event-driven, State-driven, Unwanted behavior, Optional.

### 4.1 Webhook de WhatsApp (WA)
- **EARS-WA-01 (Event-driven):** Cuando llegue un GET con `hub.mode=subscribe` y `hub.verify_token` correcto, el sistema deberá responder `hub.challenge`; si el token no coincide, 403. (US-03)
- **EARS-WA-02 (Unwanted behavior):** Si la firma `X-Hub-Signature-256` no coincide con el app secret, entonces el sistema deberá responder 403 sin procesar. (US-13)
- **EARS-WA-03 (Ubicuo):** El sistema deberá responder 200 en menos de 5 s siempre y procesar el mensaje en una background task. (US-03)
- **EARS-WA-04 (Event-driven):** Cuando llegue un mensaje con `id` ya registrado, el sistema deberá descartarlo sin efectos. (US-07)
- **EARS-WA-05 (Unwanted behavior):** Si el envío a Graph API falla, entonces el sistema deberá reintentar con backoff exponencial y registrar el fallo. (US-03)

### 4.2 Intención (INT)
- **EARS-INT-01 (Event-driven):** Cuando el texto coincida con palabras clave de `intenciones.yaml`, el sistema deberá resolver sin llamar al LLM. (US-01)
- **EARS-INT-02 (Event-driven):** Cuando no haya coincidencia y el LLM esté habilitado, el sistema deberá enviar al LLM únicamente texto, catálogo, fecha de hoy y nombre del estado. (US-13)
- **EARS-INT-03 (Unwanted behavior):** Si la respuesta del LLM no valida contra el esquema, entonces el sistema deberá descartarla completa y mostrar el menú de botones. (US-11)
- **EARS-INT-04 (Unwanted behavior):** Si hay timeout > 3 s, error del proveedor o confianza < 0.7, entonces el sistema deberá mostrar el menú de botones sin mensaje de error. (US-11)
- **EARS-INT-05 (Ubicuo):** El sistema deberá funcionar completo con `PROVEEDOR_LLM=none`. (US-11)
- **EARS-INT-06 (Ubicuo):** El sistema deberá medir y registrar el % de mensajes resueltos sin LLM. (US-10)
- **EARS-INT-07 (Ubicuo):** La intención nunca deberá causar la transición a `CONFIRMANDO` ni escribir en Calendar. (US-07)
- **EARS-INT-08 (Unwanted behavior):** Si el prompt fuera a incluir teléfono, nombre, historial o dirección, entonces el sistema deberá impedir el envío (test de contrato del prompt). (US-13)

### 4.3 Conversación (CON)
- **EARS-CON-01 (State-driven):** Mientras la clienta esté en un estado, el sistema deberá aceptar solo las transiciones definidas en SDD §8.1. (US-03)
- **EARS-CON-02 (Event-driven):** Cuando pasen 30 min sin actividad, el sistema deberá reiniciar la conversación a `INICIO`. (US-03)
- **EARS-CON-03 (Event-driven):** Cuando servicio y día se resuelvan con confianza ≥ 0.7, el sistema podrá saltar de `INICIO` a `ELIGIENDO_HORA`, nunca a `CONFIRMANDO`. (US-03)
- **EARS-CON-04 (Optional):** Donde el domicilio esté habilitado y el servicio lo permita, el sistema deberá preguntar la modalidad. (US-06)
- **EARS-CON-05 (Ubicuo):** El sistema deberá usar botones para ≤ 3 opciones y listas para servicios y horarios. (US-03)
- **EARS-CON-06 (Event-driven):** Cuando una clienta nueva escriba por primera vez, el sistema deberá mostrar el aviso de tratamiento de datos antes de pedir datos. (US-13)
- **EARS-CON-07 (Unwanted behavior):** Si la clienta ya tiene el máximo de citas futuras configurado, entonces el sistema deberá ofrecerle reagendar o cancelar en lugar de agendar. (US-05)
- **EARS-CON-08 (Ubicuo):** El sistema deberá resolver FAQ por palabras clave de `faq.yaml` sin LLM. (US-01)

### 4.4 Disponibilidad (DIS)
- **EARS-DIS-01 (Ubicuo):** El sistema deberá ofrecer un horario solo si todos sus bloques están libres, la duración del servicio termina antes del cierre y respeta anticipación mínima (2 h) y ventana máxima (21 días). (US-02)
- **EARS-DIS-02 (Ubicuo):** El sistema deberá restar de la disponibilidad los eventos de Calendar, el margen y las reservas locales PENDIENTE, CONFIRMADA y CANCELANDO. (US-02)
- **EARS-DIS-03 (Ubicuo):** El sistema deberá ofrecer como máximo 10 opciones por mensaje. (US-02)
- **EARS-DIS-04 (Ubicuo):** El sistema deberá usar la zona America/Bogota y no ofrecer días no laborales. (US-02)

### 4.5 Reservas (RES) y cola (COL)
- **EARS-RES-01 (Event-driven):** Cuando la clienta confirme, el sistema deberá insertar en **una sola transacción** la reserva PENDIENTE, sus filas de bloques y el trabajo CREAR. (US-07)
- **EARS-RES-02 (Unwanted behavior):** Si algún bloque ya está tomado, entonces el sistema deberá deshacer la transacción, informar que el horario se ocupó y reofrecer opciones. (US-07)
- **EARS-RES-03 (Unwanted behavior):** Si la clienta repite la confirmación de la misma reserva, entonces el sistema deberá devolver la reserva existente sin duplicar. (US-07)
- **EARS-RES-04 (Event-driven):** Cuando se cree la reserva, el sistema deberá responder de inmediato "cita apartada" y, al confirmar el worker, enviar la confirmación final. (US-03)
- **EARS-RES-05 (Unwanted behavior):** Si el trabajo falla definitivamente, entonces el sistema deberá liberar la reserva y avisar a la clienta. (US-07)
- **EARS-RES-06 (State-driven):** Mientras una reserva esté PENDIENTE más de 10 min sin completarse, el sistema deberá expirarla, liberar bloques y avisar. (US-07)
- **EARS-RES-07 (Event-driven):** Cuando la clienta reagende, el sistema deberá crear el evento nuevo antes de eliminar el anterior y revertir si algo falla, sin dejarla sin cita. (US-04)
- **EARS-RES-08 (Event-driven):** Cuando la clienta cancele, el sistema deberá marcar CANCELANDO y liberar bloques solo cuando el evento haya sido eliminado. (US-05)
- **EARS-RES-09 (Ubicuo):** El sistema deberá buscar las citas futuras de una clienta por `extendedProperties.private.telefono`. (US-04, US-05)
- **EARS-COL-01 (Ubicuo):** El sistema deberá tener un solo worker por `calendario_id`, nunca dos escrituras concurrentes sobre el mismo calendario. (US-07)
- **EARS-COL-02 (Ubicuo):** El sistema deberá derivar `clave_idempotencia` de teléfono, inicio, tipo y `reserva_id`, y usar un `id` de evento derivado de `reserva_id`. (US-07)
- **EARS-COL-03 (Unwanted behavior):** Si Calendar responde 5xx, 403 o 429, entonces el worker deberá reintentar con backoff exponencial, jitter ≤ 1000 ms y tope 64 s, máximo 5 intentos. (US-07)
- **EARS-COL-04 (Unwanted behavior):** Si Calendar responde 403/429 con `usageLimits`, entonces el worker deberá pausar la cola de ese calendario durante el backoff. (US-07)
- **EARS-COL-05 (Event-driven):** Antes de crear un evento, el worker deberá verificar en vivo contra Calendar que el rango está libre. (US-07, US-08)
- **EARS-COL-06 (Unwanted behavior):** Si la verificación en vivo detecta ocupación, entonces el trabajo deberá fallar sin reintentos y liberar la reserva. (US-07)

### 4.6 Caché (CAC) y calendario (CAL)
- **EARS-CAC-01 (Ubicuo):** El sistema deberá servir toda consulta de ofrecimiento desde el caché con TTL configurable (60 s por defecto); un miss deberá refrescar. (US-02)
- **EARS-CAC-02 (Event-driven):** Cuando llegue una notificación push de Calendar, el sistema deberá invalidar el caché del calendario y resincronizar. (US-08)
- **EARS-CAC-03 (Ubicuo):** El sistema deberá renovar el canal `events.watch` antes de que expire. (US-08)
- **EARS-CAC-04 (Unwanted behavior):** Si el push no está disponible, entonces el sistema deberá refrescar cada 5 min y registrar `modo_degradado`. (US-08)
- **EARS-CAC-05 (Unwanted behavior):** Si el sync token expira (410), entonces el sistema deberá hacer una resincronización completa. (US-08)
- **EARS-CAC-06 (Ubicuo):** El sistema nunca deberá crear ni modificar una cita basándose en datos cacheados. (US-07)
- **EARS-CAL-01 (Ubicuo):** Los eventos deberán incluir `extendedProperties.private` con teléfono y `reserva_id`, título con el servicio y nombre de perfil, y teléfono en la descripción. (US-08)
- **EARS-CAL-02 (Optional):** Donde la modalidad sea domicilio, el evento deberá incluir la dirección en `location`. (US-06)

### 4.7 Domicilio (DOM) y configuración (CFG)
- **EARS-DOM-01 (Optional):** Donde `domicilio.habilitado` sea true, el sistema deberá sumar el desplazamiento a los bloques ocupados y añadir el recargo de domicilio configurado al precio del servicio. (US-06, US-09)
- **EARS-DOM-02 (Unwanted behavior):** Si `domicilio.habilitado` es false, entonces el sistema no deberá mencionar ni solicitar datos de domicilio. (US-09)
- **EARS-DOM-03 (Optional):** Donde exista zona de cobertura, el sistema deberá rechazar con amabilidad direcciones fuera de ella. (US-06)
- **EARS-DOM-04 (Optional):** Donde la modalidad elegida sea domicilio, el sistema deberá mostrar a la clienta el precio del servicio, el recargo y el total antes de pedir la confirmación, y registrar el recargo en la descripción del evento. (US-06)
- **EARS-DOM-05 (Unwanted behavior):** Si `domicilio.habilitado` es true y `recargo_cop` falta o es negativo, entonces el sistema deberá fallar al arrancar con un mensaje claro. (US-09)
- **EARS-CFG-01 (Ubicuo):** El sistema deberá leer servicios, horario, mensajes, FAQ e intenciones desde `config/*.yaml` y validarlos al arrancar; si son inválidos, deberá fallar al iniciar con un mensaje claro. (US-09)
- **EARS-CFG-02 (Ubicuo):** Margen entre citas, calendario, máximo de citas por clienta, anticipación mínima de cancelación y TTL deberán ser parametrizables. (US-09)

### 4.8 Simulador (SIM) y observabilidad (OBS)
- **EARS-SIM-01 (Optional):** Donde `SIMULADOR=true`, el sistema deberá exponer `/demo`. (US-12)
- **EARS-SIM-02 (Optional):** Donde `SIMULADOR=true`, el transporte simulado deberá usar la misma lógica de conversación que WhatsApp. (US-12)
- **EARS-SIM-03 (Optional):** Donde `SIMULADOR=true`, el calendario simulado deberá actualizarse en vivo en la vista. (US-12)
- **EARS-SIM-04 (Optional):** Donde `SIMULADOR=true`, el botón de carrera deberá disparar dos confirmaciones simultáneas del mismo horario y mostrar que exactamente una gana. (US-12)
- **EARS-SIM-05 (Optional):** Donde `SIMULADOR=true`, deberá existir reinicio de estado de demo. (US-12)
- **EARS-OBS-01 (Ubicuo):** `/health` deberá exponer profundidad de cola, tasa de acierto del caché, % de mensajes sin LLM y trabajos fallidos. (US-10)

## 5. Requisitos no funcionales
| ID | Categoría | Especificación medible |
|---|---|---|
| NFR-REN-01 | Rendimiento | El webhook responde 200 en < 1 s (p95); jamás > 5 s |
| NFR-REN-02 | Rendimiento | Con caché caliente, 10 consultas del mismo día en 1 minuto generan 1 llamada a Calendar |
| NFR-REN-03 | Rendimiento | El LLM aborta a los 3 s y la clienta recibe el menú |
| NFR-DIS-01 | Disponibilidad | Con `PROVEEDOR_LLM=none` el flujo completo funciona (test E2E) |
| NFR-INT-01 | Integridad | 0 pares de reservas activas con bloques solapados (test de concurrencia con ≥ 50 intentos paralelos) |
| NFR-PRI-01 | Privacidad | El prompt al LLM no contiene PII (test de contrato) |
| NFR-PRI-02 | Privacidad | Teléfono enmascarado (últimos 4 dígitos) en logs; sin tokens ni contenido al LLM en logs de producción |
| NFR-SEG-01 | Seguridad | OWASP ASVS nivel 2 donde aplique (webhook, secretos, dependencias) |
| NFR-SEG-02 | Seguridad | Sin secretos en el repositorio; `.env.example` sin valores |
| NFR-DES-01 | Despliegue | Ejecutarse en contenedor Docker, usuario no root, `HEALTHCHECK` |
| NFR-MNT-01 | Mantenibilidad | Cero strings visibles en el código; todo en `mensajes.yaml` |
| NFR-CAL-01 | Calidad | Cobertura de ramas ≥ 85 % y 100 % en disponibilidad, reservas, cola y firma; mypy estricto y ruff limpio |
| NFR-OBS-01 | Observabilidad | Logs estructurados JSON con correlation id por mensaje |

## 6. Datos y normativa
| Dato | Clasificación | Normativa | Retención |
|---|---|---|---|
| Teléfono de la clienta | PII | Ley 1581 de 2012 | 12 meses desde la última cita |
| Nombre de perfil de WhatsApp | PII | Ley 1581 | Igual que la reserva |
| Dirección de domicilio | PII | Ley 1581 | Se borra al cerrar la cita |
| Texto del mensaje | PII potencial | Ley 1581 | No se persiste |
| Historial de citas | Interno / PII | Ley 1581 | Igual que la reserva |
| Catálogo, horarios, mensajes | Público | — | Indefinida |
| Tokens y credenciales | Secreto | — | Solo variables de entorno |
| `wa_message_id` | Interno | — | 7 días |

## 7. Casos límite
1. Dos clientas confirman el mismo horario a la vez → una gana, la otra ve "horario ocupado" y nuevas opciones. (EC-01)
2. Cita de 150 min solapada con una de 30 min que empieza dentro de ella → el segundo intento choca por bloques. (EC-02)
3. Meta reenvía el mismo webhook → un solo efecto. (EC-03)
4. La clienta toca "Confirmar" dos veces → una sola reserva. (EC-04)
5. Calendar da 429 durante la creación → backoff y un solo evento. (EC-05)
6. La dueña agenda a mano justo en el hueco ofrecido → la verificación en vivo falla; se libera y se avisa. (EC-06)
7. La clienta abandona antes de confirmar → no hay reserva; no se bloquea nada. (EC-07)
8. Worker caído o cola pausada > 10 min → la reserva PENDIENTE expira y se avisa. (EC-08)
9. LLM devuelve JSON válido pero `servicio_id` inexistente → se descarta toda la respuesta. (EC-09)
10. Texto ambiguo sobre el día → se muestra el menú o se pregunta el día con botones. (EC-10)
11. Pasan más de 24 h desde el último mensaje cuando el worker termina → solo log; la cita queda confirmada en Calendar. (EC-11)
12. Cancelar mientras la creación está en cola → CANCELANDO tras el CREAR; ELIMINAR se encola detrás. (EC-12)
13. Reagendar a un horario que se solapa con su propia cita → los bloques propios se transfieren en la misma transacción. (EC-13)
14. Cierre pocos minutos después de la última hora ofrecida → no se ofrece si la duración no cabe. (EC-14)
15. Colombia no tiene horario de verano; zona fija America/Bogota. (EC-15)
16. YAML inválido → el sistema no arranca y explica la línea errónea. (EC-16)
17. Domicilio activado con servicios que no lo permiten → solo se ofrece local. (EC-17)
18. Mensaje no textual (audio, imagen) → respuesta amable con el menú. (EC-18)
19. Clienta con el máximo de citas futuras pide agendar → se le ofrece reagendar o cancelar. (EC-19)
20. El sync token de Calendar expira (410) → resincronización completa. (EC-20)

## 8. Fuera de alcance
- ❌ Recordatorios proactivos (plantillas y costo por conversación): extensión futura.
- ❌ Pagos, anticipos y facturación.
- ❌ Múltiples manicuristas en el flujo (parametrizable a futuro por `calendario_id`).
- ❌ OAuth de usuario de Google (se usa service account).
- ❌ Celery, Redis o cola externa.
- ❌ Panel web de administración (se edita YAML).
- ❌ Streaming del LLM.
- ❌ Alta disponibilidad / múltiples instancias.
- ❌ Notificaciones a la dueña por WhatsApp (ella ve el Calendar).

## 9. Trazabilidad
| US | EARS / NFR | Sección de design.md | Tareas |
|---|---|---|---|
| US-01 | INT-01, CON-08 | §B5.4, §B7.4 | TASK-27, 38 |
| US-02 | DIS-01..04, CAC-01 | §B2.4, §B3.4 | TASK-09, 10, 12 |
| US-03 | WA-01, 03, 05; CON-01..05; RES-04 | §B5.4, §B8.4 | TASK-23, 24, 29, 41, 42, 43 |
| US-04 | RES-07, RES-09 | §B4.4 | TASK-21, 22, 26 |
| US-05 | RES-08, CON-07 | §B4.4 | TASK-20, 26 |
| US-06 | CON-04, DOM-01..05, CAL-02 | §B1.1, §B5.4 | TASK-05, 25, 45 |
| US-07 | WA-04, RES-01..06, COL-01..06, CAC-06, INT-07, NFR-INT-01 | §B4.4 | TASK-14..19 |
| US-08 | CAC-02..05, CAL-01 | §B3.4, §B9.4 | TASK-13, 45, 46, 47 |
| US-09 | CFG-01, 02; DOM-02, 05 | §B1.1 | TASK-05, 06, 49 |
| US-10 | OBS-01, INT-06 | §B8.4, §B7.4 | TASK-38, 44 |
| US-11 | INT-03..05, NFR-DIS-01 | §B7.4 | TASK-29, 36, 38 |
| US-12 | SIM-01..05 | §B6.4 | TASK-30..34 |
| US-13 | WA-02, INT-02, INT-08, CON-06, NFR-PRI-01 | §B7.4, §B8.4 | TASK-28, 39, 41 |
