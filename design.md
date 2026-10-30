# design.md — Especificación Técnica de Diseño (operativa)
## AgenteManiculista

> **Documento derivado** del `SDD.md` aprobado (§3, §6, §7, §11.2) y del `TDD.md`.
> Es el puente estricto entre requisitos y código: evita que el agente improvise.
> **Solo lectura para el agente.** No mutar arquitectura sin orden del usuario.
> Se lee **por bloque**: en el kickoff de cada bloque, solo su sección (§Bn) y las globales que cite.
> Prohibido escribir código sin haber leído antes la sección que cita la tarea.
> Todo lo marcado ⚠️ se verifica en la documentación oficial **antes** de escribir el código que lo usa.

Versión del SDD de origen: 0.3

---

## 0. Blast Radius (tabla de permisos de archivos)

| Categoría | Rutas | Permiso |
|---|---|---|
| Solo lectura | `SDD.md`, `requirements.md`, `TDD.md`, `design.md`, `AGENTS.md`, `CONSTRAINTS.md` | Leer. No modificar sin orden del usuario. |
| Prohibidos | `.env`, `.env.*` (excepto `.env.example`), `*.pem`, `*.key`, `**/secrets/**`, credenciales de la service account | No leer, crear, modificar ni subir. |
| Control (mutación limitada) | `tasks.md`, `FASE_TRACKING.md`, `PLAN_IMPLEMENTACION*.md` | Solo cambiar marcadores de estado y agregar líneas (AGENTS.md §2). |
| Bitácora | `AI_PROCESS.md` | Solo anexar. |
| Pruebas | `tests/` | Extensible: agregar specs; prohibido borrar, saltar o debilitar tests existentes. |
| Áreas de trabajo autorizadas | `app/`, `config/`, `docs/` | Crear y editar código, YAML y documentación operativa. |
| Dependencias | `pyproject.toml`, lockfile | Solo con justificación y verificación en PyPI en `AI_PROCESS.md`. |
| Infraestructura | `Dockerfile`, `docker-compose.yml`, `render.yaml`, `.github/workflows/`, `Makefile`, `.gitignore`, `.env.example` | Solo en tareas de infraestructura explícitas. |
| Documentos de proyecto | `README.md` | Solo en las tareas de documentación (Bloque 10). |

---

## 1. Arquitectura técnica (global)

### 1.1 Stack y estructura
Ver SDD §5 y TDD §1 y §2. Python 3.12, FastAPI, SQLAlchemy 2 sobre SQLite (WAL), Pydantic v2, httpx.

### 1.2 Convenciones
- Módulos y funciones en `snake_case` en español (`reservar`, `bloques_ocupados`); clases en `PascalCase`; constantes en `MAYUSCULAS`.
- Type hints en todo; `mypy --strict`. Docstring de una línea por función pública.
- Fechas internas: `datetime` con zona (UTC) y conversión a America/Bogota solo para presentar y para la cuadrícula.
- Dominio solo depende de `app/puertos.py`. Prohibido importar `fastapi`, `httpx`, `googleapiclient` o `google.genai` en `disponibilidad`, `reservas`, `cola`, `conversacion`.
- Formato de error propio: `{"codigo": "HORARIO_OCUPADO", "mensaje": "..."}`.
- Texto visible solo desde `config/mensajes.yaml` vía `app/mensajes.py` (`texto("clave", **vars)`).

### 1.3 Idempotencia, transacciones y resiliencia
- Idempotencia en tres niveles: `mensajes_procesados.wa_message_id` PK; `cola_escritura.clave_idempotencia` UNIQUE = `sha256(f"{telefono}|{inicio_iso}|{tipo}|{reserva_id}")`; `id` de evento de Calendar derivado de `reserva_id` (hex de `sha256`, alfabeto 0-9a-f; ⚠️ verificar el alfabeto y la longitud permitidos para `id` en `events.insert`). Un `409` al insertar con ese id significa "ya existe" y se trata como éxito.
- Transacciones de escritura con `BEGIN IMMEDIATE` (SQLAlchemy con `isolation_level=None` en pysqlite y evento `begin` que emite `BEGIN IMMEDIATE`; ⚠️ seguir la receta de la documentación de SQLAlchemy para SQLite). PRAGMAs: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`.
- Timeouts: Gemini 3 s; Graph 10 s; Calendar 10 s.
- Backoff: `min(64, 2**intento) s + uniform(0, 1.0) s`; máximo 5 intentos. Pausa de cola por calendario ante 403/429 con `usageLimits` (`cola_pausada_hasta`).
- Reloj inyectable (`Reloj.ahora()`); los tests nunca duermen de verdad.

### 1.4 Eventos internos (sin bus; llamadas directas)
| Evento | Emisor | Reacción |
|---|---|---|
| `reserva_apartada` | `reservas.reservar` | Responder "apartada"; invalidar caché del día |
| `reserva_confirmada` | `worker` | Enviar confirmación; invalidar caché |
| `reserva_fallida` / `reserva_expirada` | `worker` / limpieza | Liberar bloques; avisar a la clienta |
| `calendario_cambio` | push / refresco | Invalidar caché; resincronizar espejo |

### 1.5 Caché y colas
| Clave / cola | Contenido | TTL / concurrencia | Invalidación / reintentos |
|---|---|---|---|
| `cache_disponibilidad(calendario_id, fecha)` | Bloques ocupados del día | 60 s (config) | Push, escritura propia, TTL |
| `calendario_estado.sync_token` | Token incremental de `events.list` | — | 410 → resincronización completa |
| `cola_escritura` | Trabajos CREAR/ACTUALIZAR/ELIMINAR/REAGENDAR | Un worker por `calendario_id`, FIFO | 5 intentos, backoff, pausa por cuota |
| Tarea de limpieza | Expira PENDIENTE y borra `mensajes_procesados` > 7 días | Cada 60 s | — |
| Refresco degradado | Resincroniza espejo | Cada 5 min si no hay push | — |

### 1.6 Observabilidad
Logs JSON (`ts`, `nivel`, `evento`, `correlation_id`, `telefono` enmascarado `***1234`, `reserva_id`).
Nunca tokens ni texto enviado al LLM. `/health`: `cola_profundidad`, `cache_tasa_acierto`,
`mensajes_sin_llm_pct`, `trabajos_fallidos`, `modo_degradado`, `canal_expira_en`, `cola_pausada_hasta`.

---

## 2. Esquema de base de datos (global)

```sql
-- convenciones: fechas TEXT ISO-8601 UTC ('2026-11-03T15:00:00Z'); COP enteros; PRAGMAs en §1.3
CREATE TABLE conversaciones (
  telefono TEXT PRIMARY KEY,
  estado TEXT NOT NULL,
  contexto TEXT NOT NULL DEFAULT '{}',          -- JSON: servicio_id, fecha, modalidad, direccion, hora, reserva_id
  aviso_datos_aceptado INTEGER NOT NULL DEFAULT 0,
  actualizado_en TEXT NOT NULL
);
CREATE TABLE mensajes_procesados (
  wa_message_id TEXT PRIMARY KEY,
  recibido_en TEXT NOT NULL
);
CREATE TABLE reservas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendario_id TEXT NOT NULL,
  telefono TEXT NOT NULL,
  nombre_perfil TEXT,
  servicio_id TEXT NOT NULL,
  modalidad TEXT NOT NULL DEFAULT 'local' CHECK (modalidad IN ('local','domicilio')),
  direccion TEXT,
  precio_cop INTEGER NOT NULL,
  recargo_cop INTEGER NOT NULL DEFAULT 0,
  inicio TEXT NOT NULL,
  fin TEXT NOT NULL,                             -- inicio + duración (sin margen ni desplazamiento)
  estado TEXT NOT NULL CHECK (estado IN ('PENDIENTE','CONFIRMADA','CANCELANDO','CANCELADA','EXPIRADA','FALLIDA')),
  event_id TEXT,
  expira_en TEXT,
  reemplaza_a INTEGER REFERENCES reservas(id),
  creado_en TEXT NOT NULL,
  actualizado_en TEXT NOT NULL
);
CREATE TABLE reserva_bloques (                   -- una fila por bloque ocupado (duración + margen + desplazamiento)
  calendario_id TEXT NOT NULL,
  bloque_inicio TEXT NOT NULL,
  reserva_id INTEGER NOT NULL REFERENCES reservas(id),
  PRIMARY KEY (calendario_id, bloque_inicio)     -- impide el solape por arquitectura de datos
);
CREATE TABLE cola_escritura (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendario_id TEXT NOT NULL,
  tipo_operacion TEXT NOT NULL CHECK (tipo_operacion IN ('CREAR','ACTUALIZAR','ELIMINAR','REAGENDAR')),
  payload TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE','PROCESANDO','COMPLETADA','FALLIDA')),
  intentos INTEGER NOT NULL DEFAULT 0,
  proxima_ejecucion TEXT NOT NULL,
  clave_idempotencia TEXT NOT NULL UNIQUE,
  reserva_id INTEGER REFERENCES reservas(id),
  resultado TEXT,
  creado_en TEXT NOT NULL,
  actualizado_en TEXT NOT NULL
);
CREATE TABLE cache_disponibilidad (
  calendario_id TEXT NOT NULL,
  fecha TEXT NOT NULL,                           -- 'YYYY-MM-DD' en America/Bogota
  bloques_ocupados TEXT NOT NULL,                -- JSON: lista de ISO UTC
  generado_en TEXT NOT NULL,
  valido_hasta TEXT NOT NULL,
  PRIMARY KEY (calendario_id, fecha)
);
CREATE TABLE calendario_estado (
  calendario_id TEXT PRIMARY KEY,
  sync_token TEXT,
  canal_id TEXT, canal_resource_id TEXT, canal_expira_en TEXT,
  modo_degradado INTEGER NOT NULL DEFAULT 1,
  cola_pausada_hasta TEXT
);
CREATE TABLE eventos_espejo (                    -- espejo local de events.list (para ofrecer horarios)
  calendario_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  inicio TEXT NOT NULL, fin TEXT NOT NULL,
  estado TEXT NOT NULL,
  PRIMARY KEY (calendario_id, event_id)
);
CREATE TABLE metricas (nombre TEXT PRIMARY KEY, valor INTEGER NOT NULL DEFAULT 0);
```

### 2.1 Índices
`CREATE INDEX ix_reservas_tel_estado ON reservas(telefono, estado, inicio);`
`CREATE INDEX ix_cola_pend ON cola_escritura(calendario_id, estado, proxima_ejecucion, id);`
`CREATE INDEX ix_reservas_exp ON reservas(estado, expira_en);`

### 2.2 Seguridad a nivel de fila
No aplica: mono-tenant, sin acceso directo a la BD desde el exterior.

### 2.3 Reglas de bloques
- Cuadrícula: `bloque_minutos` (30) alineada a la hora local; `inicio` siempre múltiplo de la cuadrícula.
- `bloques_ocupados(servicio, modalidad) = ceil((duración + margen + (desplazamiento si domicilio)) / bloque_minutos)`.
  Ejemplo con margen 15: 45 min → 2 bloques; 75 → 3; 150 → 6; 30 → 2.
- Estados que retienen bloques: PENDIENTE, CONFIRMADA, CANCELANDO. Al pasar a CANCELADA, EXPIRADA o FALLIDA se borran sus filas.
- Reagendar: la reserva nueva toma (`UPDATE reserva_bloques SET reserva_id=nueva`) los bloques que comparte con la anterior y reclama los demás con `INSERT`. Al completar, se borran los bloques restantes de la anterior. Al revertir, los bloques de la nueva que pertenecían a la anterior vuelven a ella y el resto se borra.

---

## 3. Matriz RBAC
| Permiso / acción | Clienta | Dueña | Operador | Worker |
|---|:---:|:---:|:---:|:---:|
| Agendar / reagendar / cancelar **sus** citas | ✅ | — | — | — |
| Ver citas de otras clientas | ❌ | ✅ (Calendar) | ✅ (BD) | — |
| Escribir en Calendar | ❌ | ✅ (a mano) | ❌ | ✅ |
| Editar YAML | ❌ | ✅ | ✅ | — |
| Ver `/health` | ❌ | ❌ | ✅ | — |
| Acceder a secretos | ❌ | ❌ | ✅ | — |

Autorización en código: toda consulta de citas filtra por `telefono` de la conversación; ninguna ruta acepta un teléfono distinto del firmado por Meta.

## 4. Hardening
Verificación de firma sobre el cuerpo crudo antes de parsear JSON. Límite de tasa por IP (webhooks y `/demo`). Cabeceras `X-Content-Type-Options`, `Referrer-Policy`, `Content-Security-Policy` restrictiva en `/demo`. `/demo/*` no se registra si `SIMULADOR` no es `true`. Sin PII en `/health`. Escaneo de secretos y `pip-audit` en CI.

## 5. Docker y despliegue
| Servicio | Imagen base | Puerto | Healthcheck | Depende de |
|---|---|---|---|---|
| `app` | `python:3.12-slim` (multi-stage, versión fijada) | 8000 | `GET /health` | volumen `/data` |

- Un contenedor, `uvicorn app.main:app --workers 1`; el arranque falla si detecta más de un worker configurado.
- Usuario no root, `HEALTHCHECK`, `PYTHONDONTWRITEBYTECODE`, sin dependencias de desarrollo en runtime.
- Volumen persistente para SQLite (`DATABASE_URL=sqlite:////data/agenda.db`).
- `render.yaml`: servicio web Docker, una instancia, disco persistente, variables `SIMULADOR=true`, `PROVEEDOR_LLM=none`. ⚠️ Verificar planes y precios de Render antes del despliegue; el despliegue exige confirmación del usuario.
- `.env.example` (sin valores): `ENTORNO`, `SIMULADOR`, `PROVEEDOR_LLM`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIER`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `CALENDAR_WEBHOOK_URL`, `CALENDAR_CHANNEL_TOKEN`, `DATABASE_URL`, `LOG_LEVEL`, cada una comentada con su origen.

---

## Bloque 1 — Modelos y configuración (§B1)

### §B1.1 Tablas y archivos de configuración
Tablas de §2. Archivos YAML validados al arrancar:
```yaml
# config/negocio.yaml
calendario_id: "primary"            # correo o id del calendario compartido con la service account
zona_horaria: "America/Bogota"
bloque_minutos: 30
margen_minutos: 15
anticipacion_minima_horas: 2
ventana_maxima_dias: 21
max_citas_futuras_por_clienta: 2
cancelacion_minima_horas: 0
cache_ttl_segundos: 60
refresco_degradado_minutos: 5
reserva_pendiente_expira_minutos: 10
inactividad_reinicio_minutos: 30
horario:                             # null = no se atiende ese día
  lunes:   {abre: "08:00", cierra: "18:00"}
  martes:  {abre: "08:00", cierra: "18:00"}
  miercoles: {abre: "08:00", cierra: "18:00"}
  jueves:  {abre: "08:00", cierra: "18:00"}
  viernes: {abre: "08:00", cierra: "18:00"}
  sabado:  {abre: "09:00", cierra: "14:00"}
  domingo: null
domicilio:
  habilitado: false
  minutos_desplazamiento: 30
  recargo_cop: 0                     # obligatorio si habilitado (>= 0); lo define la manicurista
  zona_cobertura: []                 # vacío = sin restricción
ubicacion:
  texto: "Calle 00 # 00-00, Barrio Ejemplo"
  mapa_url: "https://maps.example.com"
```
```yaml
# config/servicios.yaml
servicios:
  - {id: manicure_tradicional, nombre: "Manicure tradicional", duracion_min: 45,  precio_cop: 0, descripcion: "...", domicilio_permitido: true}
  - {id: semipermanente,       nombre: "Semipermanente",       duracion_min: 75,  precio_cop: 0, descripcion: "...", domicilio_permitido: true}
  - {id: acrilicas,            nombre: "Acrílicas",            duracion_min: 150, precio_cop: 0, descripcion: "...", domicilio_permitido: false}
  - {id: retiro,               nombre: "Retiro",               duracion_min: 30,  precio_cop: 0, descripcion: "...", domicilio_permitido: true}
```
`precio_cop` de ejemplo = 0 hasta que la dueña defina precios (`⚠️` valor real por confirmar con ella; la demo usa valores de ejemplo marcados).
`config/mensajes.yaml`: mapa `clave: "texto con {variables}"`; claves obligatorias listadas en `app/mensajes.py::CLAVES_REQUERIDAS`. `faq.yaml`: lista `{palabras: [...], respuesta: clave_o_texto}`. `intenciones.yaml`: mapa `intencion: [palabras clave]`.

### §B1.2 Contratos
```python
class Horario(BaseModel): abre: time; cierra: time          # cierra > abre
class Domicilio(BaseModel):
    habilitado: bool = False
    minutos_desplazamiento: int = Field(30, ge=0)
    recargo_cop: int | None = Field(None, ge=0)             # obligatorio si habilitado
    zona_cobertura: list[str] = []
class Negocio(BaseModel): ...   # campos de §B1.1; model_config extra="forbid"
class Servicio(BaseModel): id: str; nombre: str; duracion_min: int = Field(gt=0); precio_cop: int = Field(ge=0); descripcion: str; domicilio_permitido: bool = False
class Settings(BaseSettings): entorno, simulador: bool, proveedor_llm: Literal["gemini","none"], database_url, ...
```
Validaciones: ids de servicio únicos; `duracion_min` múltiplo de 5; `bloque_minutos` divide 60; si `domicilio.habilitado` entonces `recargo_cop is not None`; días válidos. Cualquier error → `ConfiguracionInvalida` con archivo y campo.

### §B1.3 Endpoints
No aplica.

### §B1.4 Reglas de negocio y patrones
- Configuración inmutable cargada una vez (`cargar_config() -> Config`); sin lectura de YAML en caliente en MVP (reiniciar tras editar).
- Engine único con WAL y `BEGIN IMMEDIATE`; sesión por unidad de trabajo.
- Texto visible: `texto(clave, **vars)` lanza `ConfiguracionInvalida` si falta la clave al arrancar (validación de claves requeridas).

### §B1.5 Pruebas del bloque
`tests/unit/test_config.py`, `tests/unit/test_mensajes.py`, `tests/integration/test_modelos.py`. Casos: YAML válido; día sin horario; servicio duplicado; domicilio habilitado sin recargo → falla (EARS-DOM-05); `bloque_minutos` inválido; clave de mensaje faltante; PRAGMAs activos; segunda sesión no puede escribir mientras otra tiene `BEGIN IMMEDIATE`. Comando: `pytest tests/unit/test_config.py tests/unit/test_mensajes.py tests/integration/test_modelos.py -q`.

---

## Bloque 2 — Disponibilidad (§B2)

### §B2.1 Tablas
`reservas`, `reserva_bloques`, `eventos_espejo` (lectura).

### §B2.2 Contratos
```python
def bloques_ocupados(inicio: datetime, servicio: Servicio, modalidad: Modalidad, negocio: Negocio) -> list[datetime]
def horarios_ofrecibles(fecha: date, servicio: Servicio, modalidad: Modalidad, ocupados: set[datetime], ahora: datetime, negocio: Negocio, limite: int = 10) -> list[datetime]
```
`ocupados` = bloques del caché (Calendar) ∪ bloques de `reserva_bloques`.

### §B2.3 Endpoints
No aplica.

### §B2.4 Reglas
- Candidatos: múltiplos de `bloque_minutos` entre `abre` y `cierra - duración`.
- Se descarta candidato si algún bloque de `bloques_ocupados` está en `ocupados`, si `inicio < ahora + anticipación`, si `fecha > hoy + ventana` o si el día no es laborable.
- La duración (no el margen ni el desplazamiento) debe terminar ≤ `cierra`.
- Devuelve a lo sumo `limite` (10) horarios ordenados.
- Solo funciones puras; sin I/O.

### §B2.5 Pruebas
`tests/unit/test_disponibilidad.py`. Casos: conteos de bloques (45→2, 75→3, 150→6, 30→2 con margen 15); domicilio suma desplazamiento; cierre exacto; anticipación 2 h; ventana 21 días; domingo; 10 máximo; solape de 150 con 30 detectado; sin DST. Comando: `pytest tests/unit/test_disponibilidad.py -q`.

---

## Bloque 3 — Caché y espejo de eventos (§B3)

### §B3.1 Tablas
`cache_disponibilidad`, `calendario_estado`, `eventos_espejo`, `metricas`.

### §B3.2 Contratos
```python
class Calendario(Protocol):                       # app/puertos.py
    def listar_eventos(self, calendario_id: str, *, sync_token: str | None, desde: datetime, hasta: datetime) -> ResultadoListado   # ⚠️ forma real de events.list
    def ocupacion_en_vivo(self, calendario_id: str, desde: datetime, hasta: datetime, *, excluir_event_id: str | None = None) -> list[Intervalo]
    def crear_evento(self, calendario_id: str, evento: EventoNuevo) -> str
    def eliminar_evento(self, calendario_id: str, event_id: str) -> None
    def buscar_por_telefono(self, calendario_id: str, telefono: str, desde: datetime) -> list[EventoCita]
class CacheDisponibilidad:
    def ocupados_del_dia(self, calendario_id: str, fecha: date) -> set[datetime]
    def invalidar(self, calendario_id: str, fecha: date | None = None) -> None
```
`ResultadoListado`: eventos cambiados, `nuevo_sync_token`, `resincronizacion_completa: bool`. `ErrorSyncTokenExpirado` = 410.

### §B3.3 Endpoints
No aplica (el push llega en Bloque 9).

### §B3.4 Reglas
- Hit si `valido_hasta > ahora`; si no, miss: se reconstruye desde `eventos_espejo` (sincronizado con `syncToken`), sin llamar a Calendar cuando el espejo está fresco; el espejo se refresca por `events.list` + `syncToken` al expirar el TTL o al invalidar.
- Ante `ErrorSyncTokenExpirado`: se borra el espejo del calendario y se hace listado completo del rango.
- Cada consulta incrementa `cache_hit` o `cache_miss` en `metricas`.
- Escritura propia (reserva apartada/confirmada/cancelada) invalida el día afectado.
- El caché nunca se usa para decidir una escritura (la verificación va en el worker).
- Diez consultas del mismo día en un minuto → una sola llamada a `listar_eventos`.

### §B3.5 Pruebas
`tests/unit/test_cache.py`, `tests/integration/test_espejo.py`, con `FakeCalendario` que cuenta llamadas y simula 410. Comando: `pytest tests/unit/test_cache.py tests/integration/test_espejo.py -q`.

---

## Bloque 4 — Reservas, cola y worker (§B4)

### §B4.1 Tablas
`reservas`, `reserva_bloques`, `cola_escritura`, `calendario_estado`.

### §B4.2 Contratos
```python
@dataclass(frozen=True)
class SolicitudReserva: telefono: str; nombre_perfil: str | None; servicio_id: str; inicio: datetime; modalidad: Modalidad; direccion: str | None
def reservar(sesion, solicitud, negocio, ahora) -> Reserva            # lanza HorarioOcupado | LimiteCitas
def cancelar(sesion, reserva_id, telefono, ahora) -> Reserva           # PENDIENTE/CONFIRMADA -> CANCELANDO + job ELIMINAR
def reagendar(sesion, reserva_id, telefono, nuevo_inicio, ahora) -> Reserva
def expirar_pendientes(sesion, ahora) -> list[Reserva]
def citas_futuras(sesion, telefono, ahora) -> list[Reserva]
class Cola: encolar(...); reclamar_siguiente(calendario_id, ahora) -> Trabajo | None; completar(...); reintentar(...); fallar(...)
class Worker: async def correr(calendario_id)               # bucle; un solo Worker por calendario_id
```
Payload CREAR: `{reserva_id}`; ELIMINAR: `{reserva_id, event_id}`; REAGENDAR: `{reserva_nueva_id, reserva_vieja_id}`.

### §B4.3 Endpoints
No aplica.

### §B4.4 Reglas de negocio y patrones
**reservar (Unit of Work, una transacción `BEGIN IMMEDIATE`):**
1. Validar servicio, modalidad (domicilio solo si está habilitado y el servicio lo permite), cuadrícula, anticipación y ventana.
2. Contar citas futuras de la clienta (PENDIENTE/CONFIRMADA); si ≥ `max_citas_futuras_por_clienta` → `LimiteCitas`.
3. `INSERT reservas` (PENDIENTE, `expira_en = ahora + 10 min`, `precio_cop`, `recargo_cop`).
4. `INSERT reserva_bloques` por cada bloque; `IntegrityError` ⇒ rollback total y `HorarioOcupado`. Si el choque es con una reserva de la misma clienta con el mismo inicio y servicio, devolver esa reserva (idempotencia, EARS-RES-03).
5. `INSERT cola_escritura` (CREAR) con `clave_idempotencia`.
6. Commit. Invalidar caché del día.

**Worker (uno por `calendario_id`):** bucle: si `cola_pausada_hasta > ahora`, esperar; reclamar el primer PENDIENTE con `proxima_ejecucion <= ahora` por `id` (en `BEGIN IMMEDIATE`, pasa a PROCESANDO); ejecutar:
- CREAR: `ocupacion_en_vivo(rango de bloques de la reserva)`; si hay ocupación → fallo definitivo: reserva FALLIDA, bloques liberados, aviso a la clienta (EARS-COL-06). Si libre → `crear_evento` con id derivado y `extendedProperties.private {telefono, reserva_id}` (EARS-CAL-01/02); 409 = éxito. Guardar `event_id`, reserva CONFIRMADA, enviar confirmación (dentro de ventana de 24 h; si no, solo log, EC-11).
- ELIMINAR: `eliminar_evento` (404/410 = éxito); reserva CANCELADA y bloques liberados.
- REAGENDAR: verificación en vivo del rango nuevo excluyendo el evento de la reserva vieja; `crear_evento` nuevo; `eliminar_evento` viejo; vieja CANCELADA, nueva CONFIRMADA. Si falla la creación → revertir (§2.3) y la clienta conserva su cita. Si falla la eliminación tras reintentos → eliminar el evento nuevo y revertir.
- Errores: `ErrorTransitorio` (5xx/red) → reintento con backoff; `CuotaExcedida` (403/429 `usageLimits`) → reintento y `cola_pausada_hasta`; `ErrorPermanente` (4xx de datos) → FALLIDA sin reintentos. 5 intentos máx.
**cancelar:** si la reserva está PENDIENTE con CREAR en cola, el ELIMINAR se encola detrás (EC-12). Retiene bloques hasta completar.
**expirar_pendientes:** PENDIENTE con `expira_en < ahora` → EXPIRADA, cancela su trabajo si no está PROCESANDO, libera bloques, avisa.
**Arranque:** el worker se inicia en el `lifespan`; si hay trabajos PROCESANDO huérfanos, vuelven a PENDIENTE.

### §B4.5 Pruebas
`tests/integration/test_reservas.py`, `test_concurrencia.py`, `test_cola.py`, `test_worker.py`. Casos mínimos: (1) dos hilos confirman el mismo horario → una gana; (2) 50 intentos paralelos con duraciones distintas → 0 solapes; (3) mismo webhook dos veces → un evento; (4) 429 simulado → backoff, pausa y un solo evento; (5) caída tras `insert` y antes de guardar `event_id` → sin duplicado (409); (6) evento manual en el hueco → fallo definitivo y liberación; (7) reagendar con fallo al crear y al eliminar; (8) cancelar con CREAR pendiente; (9) expiración; (10) límite de citas. Comando: `pytest tests/integration -q -k "reservas or concurrencia or cola or worker"`.

---

## Bloque 5 — Máquina de estados (§B5)

### §B5.1 Tablas
`conversaciones`, `mensajes_procesados`.

### §B5.2 Contratos
```python
class Estado(StrEnum): INICIO; MENU; ELIGIENDO_SERVICIO; ELIGIENDO_MODALIDAD; ELIGIENDO_DIA; ELIGIENDO_HORA; CONFIRMANDO; GESTIONANDO_CITA; FINALIZADO
@dataclass class Entrante: telefono: str; nombre_perfil: str | None; wa_message_id: str; texto: str | None; id_interactivo: str | None; ts: datetime
@dataclass class Saliente: tipo: Literal["texto","botones","lista"]; cuerpo: str; opciones: list[Opcion]
def procesar(entrante: Entrante, ctx: Contexto) -> list[Saliente]
```
Ids interactivos: `srv:<id>`, `mod:local|domicilio`, `dia:YYYY-MM-DD`, `hora:<iso>`, `conf:si|cambiar`, `ger:reagendar|cancelar:<reserva_id>`, `menu:agendar|consultar|gestionar`.

### §B5.3 Endpoints
No aplica (transportes en Bloques 6 y 8).

### §B5.4 Reglas y patrones (State)
- Transiciones permitidas exactamente las del diagrama SDD §8.1. `ELIGIENDO_MODALIDAD` existe solo si `domicilio.habilitado` y el servicio es `domicilio_permitido`.
- `CONFIRMANDO` solo se alcanza desde un `id_interactivo` `hora:*` generado por el propio sistema en este mismo flujo (se valida contra `contexto.horas_ofrecidas`). Texto libre y LLM jamás.
- Botones para ≤ 3 opciones, listas para servicios y horas (máx. 10).
- Inactividad > 30 min → `INICIO` y contexto vacío.
- Primer contacto (`aviso_datos_aceptado=0`): mostrar aviso (clave `aviso_datos`) y pedir aceptar antes de continuar (EARS-CON-06).
- Con domicilio: pedir dirección en texto libre, validar zona si hay `zona_cobertura`, mostrar servicio + recargo = total antes de confirmar (EARS-DOM-04).
- Máximo de citas futuras alcanzado → ofrecer reagendar/cancelar (EARS-CON-07).
- Keywords y FAQ se resuelven antes del LLM, sin cambiar de estado salvo `cancelar`/`agendar`.
- Dedup: `INSERT mensajes_procesados` antes de procesar; conflicto → ignorar.
- Mensajes no textuales → respuesta con menú (EC-18).
- Todo texto sale de `mensajes.yaml`.

### §B5.5 Pruebas
`tests/unit/test_conversacion.py`, `tests/e2e/test_agendar_sin_llm.py`. Casos: tabla completa de transiciones válidas e inválidas; ninguna ruta a `CONFIRMANDO` sin hora ofrecida; inactividad; domicilio on/off; zona fuera; máximo de citas; flujo E2E de agendar con `PROVEEDOR_LLM=none`. Comando: `pytest tests/unit/test_conversacion.py tests/e2e/test_agendar_sin_llm.py -q`.

---

## Bloque 6 — Simulador (§B6)

### §B6.1 Tablas
Usa las mismas tablas; calendario en memoria (`CalendarioFalso`) con eventos visibles.

### §B6.2 Contratos
`TransporteSimulado` implementa `Mensajeria` y guarda la conversación por `telefono_demo`. `CalendarioFalso` implementa `Calendario`, con retardo opcional (para que la carrera sea visible) y modo de fallos para tests.

### §B6.3 Endpoints
| # | Método | Ruta | Auth | Request | Respuestas | Requisitos |
|---|---|---|---|---|---|---|
| 1 | GET | `/demo` | Solo `SIMULADOR=true` | — | 200 HTML | EARS-SIM-01 |
| 2 | POST | `/demo/api/mensaje` | ídem | `{telefono_demo, texto?, boton_id?}` | 200 `{respuestas:[...]}` | EARS-SIM-02 |
| 3 | GET | `/demo/api/calendario` | ídem | — | 200 eventos | EARS-SIM-03 |
| 4 | GET | `/demo/api/eventos` | ídem | — | SSE de cambios | EARS-SIM-03 |
| 5 | POST | `/demo/api/carrera` | ídem | `{inicio, servicio_id}` | 200 `{ganadora, rechazada}` | EARS-SIM-04 |
| 6 | POST | `/demo/api/reiniciar` | ídem | — | 204 | EARS-SIM-05 |

### §B6.4 Reglas
- El simulador invoca exactamente `conversacion.procesar` y el mismo `reservar`/worker; solo cambia el transporte y el calendario.
- Carrera: dos clientas ficticias (teléfonos de demo) confirman el mismo horario en paralelo (`asyncio.gather`); la UI muestra una cita confirmada y un rechazo.
- UI: chat estilo WhatsApp a la izquierda, calendario del día/semana a la derecha en vivo (SSE), botón "Dos confirmaciones simultáneas", botón "Reiniciar"; diseño vertical 9:16 amigable para grabar; contraste suficiente, objetivos táctiles ≥ 44 px.
- Datos de demo marcados como ejemplo (precios y recargo ficticios claramente indicados).
- Estas rutas no existen con `SIMULADOR` distinto de `true` (test).

### §B6.5 Pruebas
`tests/e2e/test_simulador.py`. Casos: rutas ausentes sin `SIMULADOR`; conversación completa por la API; calendario refleja la cita; la carrera deja exactamente una cita. Comando: `pytest tests/e2e/test_simulador.py -q`.

---

## Bloque 7 — Capa de intención (§B7)

### §B7.1 Archivos
`config/intenciones.yaml` (palabras clave por intención), `config/prompt_intencion.txt` (prompt versionado), `tests/evals/frases.yaml`.

### §B7.2 Contratos
```python
class Intencion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intencion: Literal["agendar","reagendar","cancelar","consultar_precio","consultar_horario","consultar_ubicacion","saludo","desconocida"]
    servicio_id: str | None
    fecha_tentativa: date | None
    franja: Literal["manana","tarde","noche"] | None
    confianza: float = Field(ge=0, le=1)
    # validador de contexto: servicio_id debe estar en el catálogo inyectado
class ProveedorLLM(Protocol):
    def interpretar(self, texto: str, catalogo: list[ServicioResumen], hoy: date, estado: str) -> Intencion | None
```
`ServicioResumen = {id, nombre}`. `Ninguno.interpretar` devuelve `None`.

### §B7.3 Endpoints
No aplica.

### §B7.4 Reglas
- Orden: 1) palabras clave (`intenciones.yaml`); 2) si no hay coincidencia y `proveedor != none`: LLM con timeout 3 s; 3) validar; 4) confianza ≥ 0.7; si algo falla → `None` y el menú de botones.
- Prompt = texto del mensaje + catálogo (id y nombre) + fecha de hoy y día de la semana + nombre del estado. **Nada más.** Una función `construir_prompt()` recibe solo esos argumentos, de modo que sea imposible pasar un teléfono (test de contrato, EARS-INT-08).
- `fecha_tentativa` imposible (ej. 2026-02-30) o `servicio_id` desconocido invalidan toda la respuesta.
- La intención solo produce candidatos; no puede originar `CONFIRMANDO` ni escribir (EARS-INT-07).
- Métrica: `mensajes_total`, `mensajes_sin_llm` en `metricas`; % expuesto en `/health` y log.
- Gemini: ⚠️ verificar en documentación oficial el nombre vigente del modelo Flash-Lite, `google-genai` y salida estructurada (`response_mime_type`, `response_schema`), el timeout por solicitud y los errores 429/cuota. Validar siempre la salida con Pydantic local.
- Logs: nunca el texto enviado al LLM en producción.

### §B7.5 Pruebas
`tests/unit/test_intencion_esquema.py`, `tests/unit/test_intencion_orquestador.py`, `tests/unit/test_prompt_privacidad.py`, `tests/evals/test_evals.py`. Casos: campo extra, fecha imposible, servicio inexistente, confianza 0.69/0.70, timeout, 429, JSON inválido, palabras clave sin llamada al LLM, `none`, prompt sin PII. Comando: `pytest tests/unit -q -k intencion tests/unit/test_prompt_privacidad.py tests/evals -q`.

---

## Bloque 8 — Integración WhatsApp y observabilidad (§B8)

### §B8.1 Tablas
`mensajes_procesados`, `metricas`.

### §B8.2 Contratos
```python
class Mensajeria(Protocol):
    def enviar_texto(self, telefono: str, texto: str) -> None
    def enviar_botones(self, telefono: str, cuerpo: str, opciones: list[Opcion]) -> None   # <= 3
    def enviar_lista(self, telefono: str, cuerpo: str, boton: str, filas: list[Opcion]) -> None  # <= 10
```
⚠️ Verificar en la documentación oficial de la WhatsApp Cloud API: forma del payload entrante (`entry[].changes[].value.messages[]`, `contacts[].profile.name`, `interactive.button_reply`/`list_reply`), forma de los mensajes salientes `interactive`, límites de longitud de títulos, filas y botones, y respuesta de error. No se escribe el adaptador sin haber leído esa documentación.

### §B8.3 Endpoints
| # | Método | Ruta | Auth | Request | Respuestas | Requisitos |
|---|---|---|---|---|---|---|
| 1 | GET | `/webhook` | verify token | `hub.mode`, `hub.verify_token`, `hub.challenge` | 200 challenge · 403 | EARS-WA-01 |
| 2 | POST | `/webhook` | `X-Hub-Signature-256` | payload de Meta | 200 siempre · 403 firma | EARS-WA-02..04 |
| 3 | GET | `/health` | Pública sin PII | — | 200 JSON | EARS-OBS-01 |

### §B8.4 Reglas
- POST: leer cuerpo crudo → verificar HMAC (`hmac.compare_digest`) → 403 si falla → `BackgroundTasks` → responder 200. El parseo y el envío ocurren en background; cualquier excepción se registra y nunca llega a Meta.
- Envío con `httpx`, timeout 10 s, reintento exponencial en 429/5xx, hasta 3 intentos (EARS-WA-05).
- `/health`: `cola_profundidad`, `cache_tasa_acierto`, `mensajes_sin_llm_pct`, `trabajos_fallidos`, `modo_degradado`, `canal_expira_en`, `cola_pausada_hasta`.
- Logging JSON con `correlation_id`; `enmascarar(telefono)` → `***1234`; filtro que elimina tokens.

### §B8.5 Pruebas
`tests/integration/test_webhook.py`, `tests/unit/test_logging.py`, `tests/integration/test_whatsapp_cliente.py`. Casos: verificación GET ok/403; firma válida/ inválida/ ausente; 200 en < 1 s con procesamiento lento; mismo `id` dos veces → un solo efecto; reintento ante 429; teléfono enmascarado; `/health` sin PII. Comando: `pytest tests/integration/test_webhook.py tests/unit/test_logging.py tests/integration/test_whatsapp_cliente.py -q`.

---

## Bloque 9 — Calendar real y push (§B9)

### §B9.1 Tablas
`calendario_estado` (canal y token), `eventos_espejo`.

### §B9.2 Contratos
Adaptador `CalendarioGoogle` que implementa `Calendario`. ⚠️ Verificar en la documentación oficial de Calendar API v3: `events.list` (`syncToken`, parámetros incompatibles, `privateExtendedProperty`), `events.insert` (campo `id`, `extendedProperties`, `location`), `events.delete`, `freebusy.query`, `events.watch` (cuerpo, `expiration`, cabeceras `X-Goog-*`), `channels.stop`, errores `403`/`429` con `usageLimits`.

### §B9.3 Endpoints
| # | Método | Ruta | Auth | Request | Respuestas | Requisitos |
|---|---|---|---|---|---|---|
| 1 | POST | `/webhook/calendar` | token de canal (cabecera) | cabeceras `X-Goog-*` | 200 · 403 | EARS-CAC-02 |

### §B9.4 Reglas
- Service account con alcance mínimo de Calendar; el calendario se comparte con "Hacer cambios en los eventos".
- Canal `events.watch` hacia `CALENDAR_WEBHOOK_URL`; si la URL no es HTTPS público válido → modo degradado (refresco cada 5 min, log `modo_degradado`).
- Renovación: tarea que crea un canal nuevo antes de la expiración (margen configurable), guarda el nuevo y detiene el anterior.
- Notificación push = "algo cambió": invalidar caché + resincronizar espejo con `syncToken`.
- Mapeo de errores a `CuotaExcedida`/`ErrorTransitorio`/`ErrorPermanente`.

### §B9.5 Pruebas
`tests/integration/test_calendario_google.py` (cliente HTTP simulado), `tests/integration/test_push.py`, `tests/integration/test_modo_degradado.py`. Casos: mapeo de errores; 410 de sync token; push inválido → 403; push válido invalida; renovación antes de expirar; degradado sin HTTPS. Comando: `pytest tests/integration/test_calendario_google.py tests/integration/test_push.py tests/integration/test_modo_degradado.py -q`.

---

## Bloque 10 — Documentación (§B10)

### §B10.1 Entregables
`README.md` (parte técnica y parte "PARA LA CLIENTA"), `docs/manual-clienta.md`, `docs/runbook.md`, `CHANGELOG.md`.

### §B10.4 Reglas
- README técnico: instalación, variables, despliegue (Docker, Render), operación de la cola, modo degradado, `GEMINI_TIER` y razón de privacidad (tier gratuito usa el contenido para mejorar productos de Google; tier de pago no ⚠️ verificar), estimación de costo mensual para 300 conversaciones (⚠️ con precios vigentes al redactar), diagrama Mermaid del flujo completo, recordatorios como extensión futura con costo por conversación.
- Parte para la clienta: sin tecnicismos; crear cuenta de WhatsApp Business, compartir el calendario con la service account, cambiar precios y horarios en los YAML, activar el domicilio y poner el recargo.

### §B10.5 Pruebas
Verificación manual guiada y `make lint` sin errores; el Mermaid se valida renderizándolo. Comando: `make lint`.
