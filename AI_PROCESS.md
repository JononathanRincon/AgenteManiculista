# AI_PROCESS.md — Bitácora de Auditoría de IA
## Agente de Agendamiento por WhatsApp para Manicurista Independiente

> Registro de todo prompt usado para generar código, documentación o diseño con IA. Cada entrada debe
> permitir reproducir o auditar qué se generó, con qué modelo y con qué instrucción exacta.
> Archivo **solo de anexado**: una entrada por interacción, en orden cronológico; nunca se edita ni
> se borra una entrada anterior. El prompt del usuario se registra de forma literal.
> La cobertura solo se reporta con la salida real de la herramienta (`--coverage`); si no se midió,
> se escribe "no medida". Nunca se rellena con 100 %.

## Registro

<!-- Las entradas se agregan debajo de esta línea, de la más antigua a la más reciente. -->

## Entrada 1 — 2026-10-30 — Diseño previo al código (fase 2, antes del SDD)

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code (app de escritorio)
- **Fase / tarea:** Fase 2 — Diseñar · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  # ROL
  Actúa como ingeniero de software senior especializado en integraciones de
  mensajería (WhatsApp Cloud API), sistemas de agendamiento con concurrencia y
  orquestación de LLMs en producción. Escribes código de producción: manejo de
  errores, backoff, logs estructurados, tests y configuración externalizada.

  # CONTEXTO DE NEGOCIO
  Cliente final: una manicurista independiente en Colombia que atiende citas
  individuales. Hoy agenda manualmente por WhatsApp mientras trabaja. El dolor
  que paga este proyecto es la CITA DOBLE: dos clientas quedan agendadas a la
  misma hora porque respondió a una mientras atendía a la otra.

  Sus clientas deben poder, por WhatsApp y sin intervención humana:
  consultar servicios/precios/ubicación, agendar viendo solo horarios reales,
  reagendar y cancelar.

  Las citas van al Google Calendar personal de la manicurista, para que las vea
  en el calendario de su celular sin instalar nada nuevo.

  # PRINCIPIO ARQUITECTÓNICO INNEGOCIABLE
  El LLM interpreta, NUNCA ejecuta.

  El LLM solo convierte lenguaje natural en una estructura JSON validada. Toda
  decisión sobre disponibilidad, toda escritura en el calendario y toda
  confirmación de cita las toma código determinista a partir de ese JSON ya
  validado. Si el LLM devuelve algo que no pasa el esquema, se descarta y se
  cae al flujo de botones.

  Un LLM que confirma citas termina alucinando horarios que no existen. Eso
  reintroduciría exactamente el problema que vinimos a resolver.

  # STACK OBLIGATORIO
  - Python 3.12, FastAPI + Uvicorn
  - WhatsApp Cloud API (Graph API v21.0+)
  - Google Calendar API v3 con SERVICE ACCOUNT; la manicurista comparte su
    calendario con permiso "Hacer cambios en los eventos". Sin OAuth de
    usuario: no voy a manejar refresh tokens de alguien no técnico.
  - Gemini API (google-genai) para la capa de intención, detrás de una
    interfaz `ProveedorLLM` con el proveedor configurable por variable de
    entorno
  - SQLite vía SQLAlchemy para estado, cola, caché e idempotencia
  - pytest, python-dotenv

  No uses Celery ni Redis. La cola va en SQLite: este sistema corre en una sola
  instancia pequeña y una dependencia más es una pieza más que se le puede
  romper a la clienta.

  # ============================================================
  # BLOQUE 1 — CAPA DE INTENCIÓN CON LLM Y FALLBACK
  # ============================================================

  ## Qué hace
  Recibe el texto libre de la clienta más un resumen del estado conversacional,
  y devuelve JSON estrictamente validado con Pydantic:

  {
    "intencion": "agendar" | "reagendar" | "cancelar" | "consultar_precio" |
                 "consultar_horario" | "consultar_ubicacion" | "saludo" |
                 "desconocida",
    "servicio_id": string | null,
    "fecha_tentativa": "YYYY-MM-DD" | null,
    "franja": "manana" | "tarde" | "noche" | null,
    "confianza": float entre 0 y 1
  }

  Cualquier campo fuera del esquema, fecha imposible o servicio_id inexistente
  en el catálogo invalida la respuesta completa.

  ## Privacidad (requisito duro)
  Al prompt del LLM va ÚNICAMENTE el texto del mensaje y el catálogo de
  servicios. NUNCA el número de teléfono, el nombre de la clienta, el historial
  de citas ni ningún identificador. El código mantiene esos datos; el LLM no
  los ve.

  Documenta esta decisión en el README y explica por qué: el free tier de
  Gemini usa el contenido enviado para mejorar los productos de Google,
  mientras que el tier de pago no. Deja la variable GEMINI_TIER en el .env y
  un párrafo en el README recomendando el tier de pago para producción, con
  estimación de costo mensual para un volumen de 300 conversaciones.

  ## Fallback obligatorio
  Si ocurre cualquiera de estas cosas, el sistema cae al menú de botones
  determinista y la clienta NUNCA ve un error:
  - timeout de más de 3 segundos
  - cuota agotada, 429 o cualquier error del proveedor
  - JSON que no valida contra el esquema
  - confianza menor a 0.7

  El fallback no es una ruta de excepción: es el camino principal del que el
  LLM es un atajo. El sistema debe funcionar completo con PROVEEDOR_LLM=none.
  Escribe un test que corra el flujo de agendar de punta a punta con el LLM
  deshabilitado y pase.

  ## Ahorro de llamadas
  Antes de llamar al LLM, intenta resolver por coincidencia de palabras clave
  contra `config/intenciones.yaml` (saludos, "precio", "cuánto vale",
  "dirección", "cancelar"). Solo si no hay coincidencia, llama al LLM. Mide y
  loguea el porcentaje de mensajes resueltos sin LLM.

  Usa Gemini Flash-Lite. No uses streaming: necesitas el JSON completo validado
  antes de actuar.

  # ============================================================
  # BLOQUE 2 — COLA DE ESCRITURA CON WORKER ÚNICO POR CALENDARIO
  # ============================================================

  ## Por qué
  La documentación de Google advierte que pueden aplicarse límites operativos
  al escribir en un mismo calendario en rápida sucesión. Además, serializar las
  escrituras hace la cita doble imposible por arquitectura, no solo por un
  constraint de base de datos.

  ## Diseño
  - Tabla `cola_escritura`: id, calendario_id, tipo_operacion (CREAR,
    ACTUALIZAR, ELIMINAR), payload JSON, estado (PENDIENTE, PROCESANDO,
    COMPLETADA, FALLIDA), intentos, proxima_ejecucion, clave_idempotencia
    UNIQUE, resultado, timestamps.
  - UN SOLO worker asíncrono por calendario_id, arrancado en el lifespan de
    FastAPI. Toma trabajos FIFO con SELECT ... FOR UPDATE simulado vía
    transacción inmediata de SQLite. Jamás dos escrituras concurrentes sobre
    el mismo calendario.
  - La `clave_idempotencia` se deriva de (telefono, fecha_hora_inicio,
    tipo_operacion). Un reintento del webhook de Meta no crea un segundo
    evento.
  - Backoff exponencial con jitter aleatorio de hasta 1.000 ms, tal como
    recomienda Google, hasta un máximo de 64 segundos. Máximo 5 intentos, luego
    FALLIDA y alerta en el log.
  - Ante 403 o 429 con usageLimits, el worker pausa la cola de ese calendario
    durante el tiempo de backoff en vez de seguir intentando trabajo por
    trabajo.

  ## Interacción con la confirmación de cita
  El flujo de confirmar es:
  1. Transacción local: INSERT en `reservas` con UNIQUE sobre
     (calendario_id, fecha_hora_inicio) para estados PENDIENTE y CONFIRMADA.
     Si falla el UNIQUE → "ese horario se acaba de ocupar", vuelve a ofrecer.
  2. Encola la creación del evento.
  3. Responde a la clienta de inmediato: cita apartada.
  4. El worker crea el evento y guarda el event_id. Si falla
     definitivamente, libera la reserva y avisa a la clienta.

  Las reservas PENDIENTE expiran a los 10 minutos por tarea de limpieza, para
  que un abandono a mitad de flujo no bloquee el horario.

  ## Tests obligatorios de este bloque
  - Dos clientas confirmando el mismo horario en paralelo: exactamente una
    gana, y la otra recibe el mensaje de horario ocupado.
  - El mismo webhook entregado dos veces por Meta produce un solo evento.
  - Un 429 simulado de Calendar reintenta con backoff y termina creando el
    evento una sola vez.

  # ============================================================
  # BLOQUE 3 — CACHÉ DE DISPONIBILIDAD CON INVALIDACIÓN POR PUSH
  # ============================================================

  ## Por qué
  Google desaconseja el sondeo repetido y recomienda notificaciones push. Diez
  clientas preguntando por el jueves en el mismo minuto deben costar una sola
  llamada a Calendar.

  ## Diseño
  - Tabla `cache_disponibilidad`: calendario_id, fecha, bloques_ocupados JSON,
    generado_en, valido_hasta. TTL por defecto 60 segundos, configurable.
  - Toda consulta de disponibilidad pasa por el caché. Un miss dispara una
    llamada `freebusy.query` y guarda el resultado.
  - Usa `sync tokens` para traer solo lo que cambió en vez del rango completo.
  - Registra un canal `events.watch` de Calendar apuntando a un endpoint
    `/webhook/calendar`. Cuando Karen agenda algo a mano desde su celular,
    Google notifica y el caché de ese calendario se invalida al instante.
  - Renueva el canal automáticamente antes de que expire; los canales de
    Calendar caducan.
  - Si el canal de push no está disponible (entorno local, HTTPS sin
    certificado válido), cae a un refresco programado cada 5 minutos y lo
    registra en el log como modo degradado.

  ## Regla crítica
  El caché sirve para OFRECER horarios. La verificación final antes de escribir
  siempre va contra Calendar en vivo, dentro del worker de la cola. Nunca se
  crea una cita confiando en datos cacheados.

  # REQUISITOS FUNCIONALES (sin cambios respecto a la versión anterior)

  ## Catálogo de servicios — `config/servicios.yaml`
  id, nombre, duración en minutos, precio COP, descripción corta. Inicial:
  manicure tradicional (45), semipermanente (75), acrílicas (150), retiro (30).

  ## Horario — `config/negocio.yaml`
  Días laborales, apertura y cierre por día, bloque de agendamiento (30 min),
  margen entre citas, zona horaria America/Bogota fija, anticipación mínima
  (2 horas), ventana máxima hacia adelante (21 días).

  ## Cálculo de disponibilidad
  Resta bloques ocupados, margen y reservas PENDIENTE locales. Un horario solo
  se ofrece si el servicio cabe completo antes del cierre. Máximo 10 opciones
  por mensaje: es el límite de filas de un interactive list de WhatsApp.

  ## Máquina de estados
  INICIO, MENU, ELIGIENDO_SERVICIO, ELIGIENDO_DIA, ELIGIENDO_HORA, CONFIRMANDO,
  GESTIONANDO_CITA, FINALIZADO. Persistida por teléfono con timestamp. Reinicia
  tras 30 minutos de inactividad. La capa de intención puede saltar estados
  cuando la confianza lo permite (de INICIO directo a ELIGIENDO_HORA si la
  clienta ya dijo servicio y día), pero nunca saltar CONFIRMANDO.

  Botones para 3 o menos opciones, listas para horarios y servicios.

  ## Reagendar y cancelar
  Busca citas futuras del número en `extendedProperties.private.telefono` del
  evento. Reagendar = una sola unidad de trabajo en la cola, que crea la nueva
  antes de eliminar la anterior y revierte si algo falla. La clienta nunca
  queda sin cita.

  ## Preguntas frecuentes — `config/faq.yaml`
  Pares pregunta/respuesta por palabras clave, sin LLM.

  # RESTRICCIONES DE META
  - Ventana de servicio de 24 horas: fuera de ella solo plantillas aprobadas.
    Todo el flujo reacciona a mensajes de la clienta. No implementes
    recordatorios proactivos; documéntalos como extensión futura explicando
    que generan costo por conversación.
  - Webhook GET con hub.mode, hub.verify_token, hub.challenge.
  - Valida X-Hub-Signature-256 con el app secret. 403 si no coincide.
  - Responde 200 en menos de 5 segundos SIEMPRE; procesa en background task.
  - Deduplica por el `id` del mensaje entrante.

  # CALIDAD
  - Todo texto visible vive en `config/mensajes.yaml`, español colombiano,
    tuteando. Cero strings hardcodeados.
  - Logging estructurado con el teléfono enmascarado (últimos 4 dígitos).
    Nunca loguees tokens ni el contenido enviado al LLM en producción.
  - Type hints en todo. Reintento exponencial hacia Graph API, Calendar y
    Gemini.
  - Métricas expuestas en `/health`: profundidad de la cola, tasa de acierto
    del caché, porcentaje de mensajes resueltos sin LLM, trabajos fallidos.

  # ENTREGABLES
  1. Estructura:
     app/ (main.py, webhook.py, conversacion.py, intencion.py, llm/, cola.py,
     worker.py, cache.py, disponibilidad.py, calendario.py, whatsapp.py,
     modelos.py, config.py)
     config/ (servicios, negocio, mensajes, faq, intenciones .yaml)
     tests/
     .env.example comentado con el origen de cada variable
     README.md
     Makefile: install, run, test, lint
  2. README en dos partes separadas:
     - Técnica: instalación, despliegue, variables, operación de la cola.
     - PARA LA CLIENTA, sin tecnicismos: crear la cuenta de WhatsApp Business,
       compartir su calendario, y cambiar precios y horarios editando los
       YAML. Esta parte la entrego como manual.
  3. Un diagrama en Mermaid dentro del README mostrando el flujo completo:
     mensaje entrante → dedup → intención (LLM o fallback) → máquina de
     estados → caché → cola → Calendar.

  # MODO SIMULADOR (constrúyelo primero)
  Con `SIMULADOR=true`, expón en `/demo` una interfaz web local: ventana de
  chat que imita WhatsApp a la izquierda, vista del calendario a la derecha
  actualizándose en vivo. Misma lógica de conversación; solo cambia el
  transporte. Incluye un botón que dispare dos confirmaciones simultáneas sobre
  el mismo horario, para poder demostrar visualmente que el sistema impide la
  cita doble.

  Lo necesito para grabar un video de demostración antes de tener credenciales
  de Meta aprobadas. Debe verse presentable en una grabación vertical de
  pantalla.

  # CÓMO QUIERO QUE TRABAJES
  1. Antes de escribir código, razona paso a paso y muéstrame: modelo de datos,
     máquina de estados, el esquema JSON de la capa de intención, y el
     recorrido exacto de una confirmación desde el mensaje hasta el evento
     creado, señalando en qué punto es imposible la cita doble. Espera mi
     visto bueno.
  2. Construye en este orden, corriendo los tests después de cada bloque:
     modelos y config → disponibilidad → caché → cola y worker → máquina de
     estados → modo simulador → capa de intención → integración WhatsApp →
     integración push de Calendar → README.
  3. Si una decisión depende de información que no te di (si atiende a
     domicilio, si hay más de una manicurista), pregúntame en vez de asumir.
  4. No inventes endpoints ni campos de la Graph API, de Calendar ni del SDK de
     Gemini. Si no estás seguro de la forma exacta de un payload, dilo y
     consúltalo en la documentación oficial antes de escribirlo.
  ```
- **Output del agente (mensaje final):**
  ```text
  Presentó el diseño previo (modelo de datos, máquina de estados, esquema de intención, recorrido de la
  confirmación) con tres correcciones: (1) UNIQUE por inicio no impide solapes de distinta duración, se
  propone UNIQUE por bloque; (2) los sync tokens pertenecen a events.list, no a freebusy; (3) la clave de
  idempotencia necesita reserva_id. Se señaló que SELECT FOR UPDATE no existe en SQLite (BEGIN IMMEDIATE) y
  que se requiere un solo proceso. Se formularon 8 preguntas de negocio y se listó lo pendiente de verificar
  en documentación oficial.
  ```
- **Archivos creados o modificados:** Ninguno en esta entrada.
- **Patrones y principios aplicados:** Arquitectura hexagonal, outbox/cola transaccional, idempotencia, caché con invalidación por eventos.
- **Comandos de terminal ejecutados:** Ninguno (`Glob` confirmó carpeta vacía y sin `AGENTS.md`).
- **Acciones de Git:** Ninguna (repositorio no inicializado).
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica (sin código).
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** Sin tasks.md aún (bloqueado por el SDD)
- **Pendientes o riesgos:** Aprobación del diseño por el usuario; verificación de APIs en documentación oficial.

## Entrada 2 — 2026-10-30 — Respuestas a las 8 preguntas de diseño

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Diseñar · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  hazme las 8 preguntas
  ```
  Respuestas del usuario (literales): "Orden de la lista"; "Ambos" (local y domicilio); "eso se puede parametrizar"
  (calendarios); "que tambien sea parametrizado" (margen); título con nombre de perfil y teléfono en descripción: Sí;
  reglas de cancelación: "Sin mínimo, máx. 2 citas"; mensajes: "la opcion recomendada respectando el limite de
  comunicacion de meta para que no cobre de mensajes escritos por el agente"; hosting: sin preferencia.
- **Output del agente (mensaje final):**
  ```text
  Resumió las 8 decisiones y señaló el impacto del domicilio (estado de modalidad, dirección, desplazamiento en
  bloques) con tres preguntas derivadas (desplazamiento, recargo, zona).
  ```
- **Archivos creados o modificados:** Ninguno.
- **Patrones y principios aplicados:** N/A
- **Comandos de terminal ejecutados:** Ninguno.
- **Acciones de Git:** Ninguna.
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica.
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** N/A
- **Pendientes o riesgos:** PA-02 (valores de domicilio).

## Entrada 3 — 2026-10-30 — Domicilio parametrizable y plataforma de la demo

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Diseñar · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  que el proceso de domicilio sea parametrizable por el desarrollador o por la dueña, si desean añadir esa funcionalidad dentro del flujo,  donde me recomiendas desplegar una version como demo para mostral al cliente, yo trabajo con vercel, hay se puede desplegar
  ```
- **Output del agente (mensaje final):**
  ```text
  Domicilio como interruptor en negocio.yaml. Vercel descartado (funciones efímeras, disco de solo lectura, varias
  instancias rompen el worker único); se recomienda Render (una instancia, disco persistente, HTTPS), con demo
  en modo simulador sin credenciales.
  ```
- **Archivos creados o modificados:** Ninguno.
- **Patrones y principios aplicados:** Feature flag por configuración.
- **Comandos de terminal ejecutados:** Ninguno.
- **Acciones de Git:** Ninguna.
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica.
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** N/A
- **Pendientes o riesgos:** Precios vigentes de Render por verificar antes de crear el servicio.

## Entrada 4 — 2026-10-30 — Redacción del SDD.md v0.1

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code (skill `harness-proyecto`)
- **Fase / tarea:** Fase 2 — Diseñar, Etapa A · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  okey desplegarlo el render entonces
  ```
- **Output del agente (mensaje final):**
  ```text
  Aclaró que no hay nada que desplegar aún (sin código), tomó el "okey" como aprobación del diseño y de Render
  como destino de la demo (ADR-007), y redactó SDD.md v0.1 con las 21 secciones. Pendiente la validación humana
  (§20). No se crean documentos derivados ni código hasta la aprobación.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `SDD.md` | 1-fin | Creado, versión 0.1, estado Borrador |
  | `AI_PROCESS.md` | 1-fin | Creado con las entradas 1 a 4 |
- **Patrones y principios aplicados:** Arquitectura hexagonal, SDD-first, EARS, STRIDE.
- **Comandos de terminal ejecutados:** Ninguno.
- **Acciones de Git:** Ninguna (git se inicializa en la fase 3, tras aprobar el SDD).
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica (sin código).
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** SDD en Borrador; compuerta §20 abierta.
- **Pendientes o riesgos:** Preguntas abiertas PA-02, PA-04 a PA-11; `CONSTRAINTS.md` pendiente de crear con `/agent-skills:constraints` (se ajusta al aprobar el stack).

## Entrada 5 — 2026-10-30 — SDD v0.2: recargo de domicilio y cierre de preguntas

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Diseñar, Etapa A · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  estoy de acuerdo con tus recomendaciones, pero este punto PA-02	Valores del domicilio	30 min de desplazamiento, aplicados una sola vez, sin recargo y sin zona. Todo parametrizable... concidero que se debe añadir un recargo y ese recargo es parametrizable segun la manicurista
  ```
- **Output del agente (mensaje final):**
  ```text
  SDD pasa a v0.2: recargo de domicilio obligatorio y parametrizable (EARS-DOM-01/04/05), se cierran PA-02,
  PA-04, PA-05, PA-06 y PA-08 con los defaults; se abre PA-15 (monto del recargo). Siguen abiertas sin bloquear:
  PA-07, PA-09, PA-10, PA-11, PA-15.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `SDD.md` | §4.5, §6.5, §10.7, §18, §21 | v0.1 a v0.2 |
- **Patrones y principios aplicados:** Feature flag por configuración con validación al arranque.
- **Comandos de terminal ejecutados:** `cat >> AI_PROCESS.md` (anexado de esta entrada)
- **Acciones de Git:** Ninguna.
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica (sin código).
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** SDD en Borrador; compuerta §20 abierta.
- **Pendientes o riesgos:** Aprobación humana del SDD.

## Entrada 6 — 2026-10-30 — SDD v0.3: cierre de preguntas restantes

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Diseñar, Etapa A · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  esos punto estoy de acuerdo con tus recomendaciones
  ```
- **Output del agente (mensaje final):**
  ```text
  SDD v0.3: PA-07, PA-09, PA-10, PA-11 y PA-15 cerradas como acciones diferidas (go-live o cada bloque). No se
  marcó el checklist §20 ni se registró aprobación: la compuerta sigue abierta hasta que el usuario la apruebe
  de forma explícita.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `SDD.md` | §18, §21, cabecera | v0.2 a v0.3 |
- **Patrones y principios aplicados:** N/A
- **Comandos de terminal ejecutados:** `cat >> AI_PROCESS.md`
- **Acciones de Git:** Ninguna.
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica (sin código).
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** SDD en Borrador; compuerta §20 abierta.
- **Pendientes o riesgos:** Aprobación humana explícita del SDD.

## Entrada 7 — 2026-10-30 — Aprobación del SDD v0.3 y generación de documentos derivados

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code (skill `harness-proyecto`, Etapa B)
- **Fase / tarea:** Fase 0.3 y Fase 1 — Documentos derivados · N/A (documentación)
- **Prompt utilizado (literal):**
  ```text
  Apruebo SDD v0.3
  ```
- **Output del agente (mensaje final):**
  ```text
  Aprobación registrada en SDD §20.2 (estado Aprobado, checklist §20.1 marcado por el agente por instrucción
  explícita del usuario). Se generaron CONSTRAINTS.md, requirements.md, TDD.md, design.md (con Blast Radius),
  PLAN_IMPLEMENTACION.md, tasks.md (62 tareas en 12 bloques), FASE_TRACKING.md, AGENTS.md, CLAUDE.md,
  docs/threat-model.md y docs/adr/0001-decisiones-iniciales.md. No se inicializó git ni se escribió código:
  pendiente la revisión del usuario de los derivados (Fase 1.7).
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `SDD.md` | cabecera, §20 | Estado Aprobado; checklist y registro de aprobación |
  | `CONSTRAINTS.md`, `requirements.md`, `TDD.md`, `design.md` | todo | Creados |
  | `PLAN_IMPLEMENTACION.md`, `tasks.md`, `FASE_TRACKING.md` | todo | Creados |
  | `AGENTS.md`, `CLAUDE.md` | todo | Creados |
  | `docs/threat-model.md`, `docs/adr/0001-decisiones-iniciales.md` | todo | Creados |
- **Patrones y principios aplicados:** SDD-first, trazabilidad US → EARS → design → TASK, hexagonal por puertos, Blast Radius.
- **Comandos de terminal ejecutados:** `cat >> AI_PROCESS.md`
- **Acciones de Git:** Ninguna (TASK-01 inicializa GitFlow tras la revisión del usuario).
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:** No aplica (sin código). Cobertura: no medida.
- **Casos ejecutados:** No aplica.
- **Intentos del bucle interno:** N/A
- **Estado actualizado:** FASE_TRACKING: Fase 0 `[x] ✅`; Fase 1 `⏳` (falta 1.7 revisión del usuario).
- **Pendientes o riesgos:** Revisión del usuario de los derivados; versiones de librerías y APIs se verifican por tarea.

## Entrada 8 — 2026-10-30 — Aprobación de derivados y TASK-01

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 1.7 (cierre) y Fase 2 — Infraestructura · TASK-01
- **Prompt utilizado (literal):**
  ```text
  Apruebo derivados
  ```
- **Output del agente (mensaje final):**
  ```text
  Fase 1 cerrada. Repositorio inicializado con main y develop, .gitignore que bloquea secretos y bases locales,
  estructura de carpetas y primer commit con los documentos del harness. Trabajo continúa en develop.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `.gitignore` | 1-fin | Creado (bloquea `.env*`, `*.pem`, `*.key`, `*.db`, service-account) |
  | `app/ config/ tests/{unit,integration,e2e,evals}/ docs/adr/` | — | Carpetas con `.gitkeep` |
  | `FASE_TRACKING.md`, `PLAN_IMPLEMENTACION.md`, `tasks.md` | marcadores | 1.7 y TASK-01 a `[x]`; Fase 1 ✅; Fase 2 ⏳ |
- **Patrones y principios aplicados:** GitFlow; secretos fuera del repositorio.
- **Comandos de terminal ejecutados:**
  ```bash
  git init -b main                       # repositorio creado
  git commit (initial)                   # 19d0692 en main
  git branch develop; git checkout develop
  git branch --list main develop         # main y develop presentes
  ```
- **Acciones de Git:**
  - Rama: commit inicial directo en `main` (único caso: el repositorio aún no tenía `develop`); luego `develop` desde `main`.
  - Commits: `19d0692 chore: documentos del harness y estructura inicial`
  - Merge: no aplica
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:**
  | Suite / nivel | Total | ✅ Pasaron | ❌ Fallaron | Cobertura ramas (medida) |
  |---|---|---|---|---|
  | Test de la tarea (`git branch --list main develop`) | 1 | 1 | 0 | no aplica |
- **Casos ejecutados:**
  - [x] `git branch --list main develop`: ambas ramas existen (NFR-SEG-02, NFR-DES-01)
  - [x] `git status --short` sin secretos staged
- **Intentos del bucle interno:** 1/3
- **Estado actualizado:** tasks.md TASK-01 `[x]` · FASE_TRACKING Fase 1 `✅`, Fase 2 `⏳`
- **Pendientes o riesgos:** Python local es 3.14.2 y el proyecto exige 3.12: TASK-02 debe resolverlo (verificar el lanzador `py -3.12` o usar Docker). Los commits y merges de las tareas siguientes llevan la línea Co-Authored-By.

## Entrada 9 — 2026-10-30 — TASK-02: herramientas de calidad y dependencias

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Infraestructura · TASK-02
- **Prompt utilizado (literal):**
  ```text
  Instalar uv y Python 3.12 (Recomendado)   [respuesta del usuario a la pregunta de cómo resolver Python 3.12]
  ```
- **Output del agente (mensaje final):**
  ```text
  pyproject.toml (ruff, mypy estricto, pytest, cobertura de ramas con umbral 85 %), Makefile (install, run, test, lint, ci),
  uv.lock con versiones fijadas, prueba de humo. make lint y make test en verde.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `pyproject.toml`, `uv.lock` | todo | Creados; dependencias fijadas por lockfile |
  | `Makefile` | todo | Targets install, run, test, lint, ci |
  | `app/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_humo.py` | todo | Esqueleto y prueba de humo |
- **Patrones y principios aplicados:** Quality gate por herramienta; lockfile reproducible.
- **Comandos de terminal ejecutados:**
  ```bash
  python -m pip install --user uv==0.13.6          # uv 0.13.6 (paquete oficial de Astral en PyPI)
  uv python install 3.12                           # CPython 3.12.16 en C:/Users/Galiatech/uv-python (UV_PYTHON_INSTALL_DIR);
                                                   # la ruta por defecto falló al crear el enlace de versión menor
  uv add ... ; uv add --dev ...                    # resolución contra PyPI
  make lint ; make test                            # verde
  ```
- **Acciones de Git:**
  - Rama: `feature/TASK-02-herramientas-calidad`
  - Commits: ver merge en develop
  - Merge: ver `git log` de develop
- **Dependencias agregadas (verificadas en PyPI por resolución de uv; nombres canónicos de proyectos oficiales):**
  | Paquete | Versión fijada | Justificación |
  |---|---|---|
  | fastapi | 0.162.0 | API y webhooks (obligatorio por el cliente) |
  | uvicorn | 0.54.0 | Servidor ASGI (obligatorio) |
  | sqlalchemy | 2.1.6 | ORM sobre SQLite (obligatorio) |
  | pydantic, pydantic-settings | 2.14.2, 2.16.2 | Esquemas estrictos y configuración |
  | httpx | 0.28.1 | Cliente de Graph API con timeouts |
  | pyyaml | 6.0.3 | Lectura de `config/*.yaml` |
  | python-dotenv | 1.2.4 | Carga de `.env` (obligatorio) |
  | google-api-python-client, google-auth | 2.201.0, 2.62.0 | Calendar API v3 con service account |
  | google-genai | 2.34.0 | Capa de intención con Gemini |
  | dev: ruff 0.17.3, mypy 2.4.1, pytest 9.2.0, pytest-asyncio 1.4.1, pytest-cov 7.1.0, pip-audit 2.10.1, types-pyyaml | — | Calidad: lint, tipos, pruebas, cobertura, auditoría |
- **Reporte de pruebas:**
  | Suite / nivel | Total | ✅ Pasaron | ❌ Fallaron | Cobertura ramas (medida) |
  |---|---|---|---|---|
  | Unitarias (humo) | 2 | 2 | 0 | 100 % (0 sentencias; sin código de dominio aún) |
  | **Total** | 2 | 2 | 0 | 100 % |
- **Casos ejecutados:**
  - [x] `tests/unit/test_humo.py`: paquete importable y Python 3.12 (NFR-CAL-01)
- **Intentos del bucle interno:** 1/3 (el aviso de pytest-asyncio se corrigió fijando el scope del loop)
- **Estado actualizado:** tasks.md TASK-02 `[x]`
- **Pendientes o riesgos:** El Python 3.12 gestionado por uv vive fuera del proyecto; `make install` lo necesita en `UV_PYTHON_INSTALL_DIR` en este equipo. Python 3.14 del sistema no se usa. `google-genai 2.34.0` y demás APIs se verifican en documentación al usarlas.

## Entrada 10 — 2026-10-30 — TASK-03: Dockerfile multi-stage y compose de desarrollo

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Infraestructura · TASK-03
- **Prompt utilizado (literal):**
  ```text
  Apruebo derivados   [continuación del loop: siguiente tarea del Bloque 0]
  ```
- **Output del agente (mensaje final):**
  ```text
  Dockerfile multi-stage (builder con uv y lockfile, runtime mínimo), usuario no root uid 10001, HEALTHCHECK sobre /health,
  uvicorn con --workers 1, volumen /data para SQLite. docker-compose.yml de desarrollo, .dockerignore y .env.example comentado sin valores.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example` | todo | Creados |
- **Patrones y principios aplicados:** Multi-stage, mínimo privilegio, secretos solo por entorno, imagen base fijada (`python:3.12.16-slim`, tag verificado con `docker manifest inspect`).
- **Comandos de terminal ejecutados:**
  ```bash
  docker manifest inspect python:3.12.16-slim   # existe
  docker compose build                          # OK
  docker image inspect ...                      # Usuario "app"; HEALTHCHECK declarado
  ```
- **Acciones de Git:** Rama `feature/TASK-03-docker`; commit y merge ver `git log` de develop.
- **Dependencias agregadas:** Ninguna (uv 0.13.6 solo dentro de la etapa builder de la imagen).
- **Reporte de pruebas:**
  | Suite / nivel | Total | ✅ Pasaron | ❌ Fallaron | Cobertura ramas (medida) |
  |---|---|---|---|---|
  | `docker compose build` | 1 | 1 | 0 | no aplica |
- **Casos ejecutados:**
  - [x] Build de la imagen; usuario no root; HEALTHCHECK presente (NFR-DES-01)
- **Intentos del bucle interno:** 1/3
- **Estado actualizado:** tasks.md TASK-03 `[x]`
- **Pendientes o riesgos:** "Servicios healthy" del criterio de la tarea se verifica cuando exista `app/main.py` con `/health` (TASK-31/44); hoy el contenedor no arranca por falta de la app. `.env.example` se valida contra `Settings` en TASK-06. Trivy/SBOM quedan para el pipeline.

## Entrada 11 — 2026-10-30 — TASK-04: CI y cierre del Bloque 0 (Fase 2)

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 2 — Infraestructura · TASK-04
- **Prompt utilizado (literal):**
  ```text
  Apruebo derivados   [continuación del loop: última tarea del Bloque 0]
  ```
- **Output del agente (mensaje final):**
  ```text
  Workflow de GitHub Actions (calidad, secretos con gitleaks, imagen con Trivy), versiones de acciones fijadas tras consultar sus tags,
  target make ci (lint + test + pip-audit) y red de seguridad local de secretos en pytest. make ci pasa localmente. Bloque 0 cerrado.
  ```
- **Archivos creados o modificados:**
  | Archivo | Líneas | Cambio |
  |---|---|---|
  | `.github/workflows/ci.yml` | todo | Creado |
  | `tests/unit/test_sin_secretos.py` | todo | Creado: archivos de secretos y patrones conocidos en archivos versionados |
  | `.gitignore` | 40 | `requirements-ci.txt` |
  | `FASE_TRACKING.md`, `PLAN_IMPLEMENTACION.md`, `tasks.md` | marcadores | Fase 2 `✅`; Fase 3 `⏳`; TASK-04 `[x]` |
- **Patrones y principios aplicados:** Defensa en profundidad para secretos (gitleaks en CI + prueba local); mínimo privilegio (`permissions: contents: read`).
- **Comandos de terminal ejecutados:**
  ```bash
  git ls-remote --tags ...      # checkout v7.0.1, setup-uv v10.3.3, gitleaks-action v3.0.0, trivy-action v0.36.0
  make UV="python -m uv" ci     # lint OK, mypy OK, 4 pruebas OK, pip-audit sin vulnerabilidades conocidas
  ```
- **Acciones de Git:** Rama `feature/TASK-04-ci`; commit y merge ver `git log` de develop.
- **Dependencias agregadas:** Ninguna
- **Reporte de pruebas:**
  | Suite / nivel | Total | ✅ Pasaron | ❌ Fallaron | Cobertura ramas (medida) |
  |---|---|---|---|---|
  | Unitarias | 4 | 4 | 0 | 100 % (0 sentencias; sin código de dominio aún) |
  | **Total** | 4 | 4 | 0 | 100 % |
- **Casos ejecutados:**
  - [x] `test_sin_secretos.py`: sin `.env`/`.pem`/`.key` versionados; sin patrones de claves (NFR-SEG-02)
  - [x] `make ci`: ruff, ruff format, mypy estricto, pytest con cobertura de ramas, pip-audit (NFR-CAL-01, NFR-SEG-01)
- **Intentos del bucle interno:** 1/3
- **Estado actualizado:** tasks.md TASK-04 `[x]` · FASE_TRACKING Fase 2 `✅`, Fase 3 `⏳`
- **Pendientes o riesgos:** El workflow aún no se ejecutó (no hay remoto en GitHub, PA-09). `gitleaks-action` puede requerir licencia en organizaciones de GitHub (gratis en cuentas personales): verificar al elegir el remoto. Trivy y SBOM quedan para la primera ejecución del pipeline. Bloque 0 sin revisión de código dedicada (no hay lógica de dominio); la revisión formal comienza en el Bloque 1.

## Entrada 12 — 2026-10-30 — Bloque 1 completo: TASK-05, 06, 07, 08 y 52 (Fase 3)

- **Modelo/herramienta:** Claude Sonnet 5.5 en Claude Code
- **Fase / tarea:** Fase 3 — Modelos y configuración · TASK-05 a TASK-08 y TASK-52
- **Prompt utilizado (literal):**
  ```text
  hagas todos los bloques de una vez, avisame cuando termines
  ```
  (El usuario autorizó ejecutar todos los bloques seguidos sin pausas intermedias; se mantiene rama, gate, commit y merge por tarea. Push, despliegue en Render y release a main siguen requiriendo confirmación.)
- **Output del agente (resumen):** `app/config.py` (modelos Pydantic estrictos e inmutables, `cargar_config`, `ConfiguracionInvalida`, `Settings`), `config/negocio.yaml`, `config/servicios.yaml`, `app/modelos.py` (ORM de design.md §2, `FechaUTC`, `crear_engine` con WAL/busy_timeout/foreign_keys/synchronous, `unidad_de_trabajo` con BEGIN IMMEDIATE solo en escrituras), `app/mensajes.py` y `config/mensajes.yaml` (claves requeridas, plantillas validadas).
- **Archivos:** app/config.py, app/modelos.py, app/mensajes.py, config/*.yaml, tests/unit/test_config.py, test_settings.py, test_mensajes.py, tests/integration/test_modelos.py, `.env.example` sin cambios (la prueba verifica que coincide con `Settings`).
- **Documentación oficial consultada (source-driven):** SQLAlchemy 2.0, dialecto SQLite/pysqlite: receta `isolation_level=None` en el evento `connect` y `BEGIN` emitido en el evento `begin` (aquí `BEGIN IMMEDIATE` vía opción de ejecución `inmediata`); la página solo documenta `BEGIN`, la variante IMMEDIATE se verificó con la prueba de bloqueo.
- **Dependencias agregadas:** `tzdata` 2026.6 (paquete oficial de la PSF, github.com/python/tzdata; verificado en PyPI). Justificación: Windows y la imagen `python:3.12-slim` no traen base de zonas horarias y se necesita `America/Bogota`.
- **Patrones:** configuración inmutable validada al arrancar; Unit of Work; TypeDecorator para fechas UTC.
- **Reporte de pruebas:** 44 pasaron, 0 fallaron. Cobertura de ramas medida 99,15 % (config 98 %, mensajes 100 %, modelos 100 %).
- **Hallazgos de la revisión (TASK-52, autorrevisión de 5 ejes):** sin críticos ni altos. Observación media: `unidad_de_trabajo` hace commit aunque la escritura no se haya emitido (inofensivo). Observación baja: `Settings.gemini_model` trae un valor por defecto que se debe verificar en la documentación oficial en TASK-37.
- **Intentos del bucle interno:** TASK-07 2/3 (la transacción de SQLite empieza con el primer SQL, no con `add`; se corrigió la prueba con `flush`); resto 1/3.
- **Estado actualizado:** tasks.md TASK-05 a 08 y 52 `[x]`; FASE_TRACKING Fase 3 `✅`.
- **Pendientes:** el shell del entorno falla con heredocs que contienen comillas; los archivos se escriben con la herramienta Write.
