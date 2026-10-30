"""Agente conversacional determinista: procesa un mensaje y devuelve las respuestas.

Mismo código para WhatsApp y para el simulador (EARS-SIM-02). La decisión de agendar, reagendar o
cancelar la toma este código con datos validados; ningún LLM puede llegar a CONFIRMANDO.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.agenda import dias_con_cupo, horas_libres
from app.cache import CacheDisponibilidad
from app.config import DIAS, Config, Modalidad, Servicio
from app.conversacion import (
    Contexto,
    Entrante,
    Estado,
    Saliente,
    Sesion,
    cargar_sesion,
    guardar_sesion,
)
from app.formato import fecha, fecha_corta, hora, hora_reloj, precio
from app.intencion import Reglas, cargar_reglas, resolver_faq, resolver_por_palabras_clave
from app.mensajes import texto
from app.modelos import MensajeProcesado, Reserva, unidad_de_trabajo
from app.puertos import Opcion, Reloj
from app.reservas import (
    HorarioOcupado,
    LimiteCitas,
    ReservaInvalida,
    ReservaNoEncontrada,
    SolicitudReserva,
    cancelar,
    citas_futuras,
    contar_futuras,
    reagendar,
    reservar,
)

log = logging.getLogger("agente")

Atajo = Callable[[str], "list[Saliente] | str | None"]


def _opciones_menu() -> list[Opcion]:
    return [
        Opcion("menu:agendar", texto("menu_agendar")),
        Opcion("menu:consultar", texto("menu_consultar")),
        Opcion("menu:gestionar", texto("menu_gestionar")),
    ]


class Agente:
    def __init__(
        self,
        engine: Engine,
        config: Config,
        cache: CacheDisponibilidad,
        reloj: Reloj,
        atajo: Atajo | None = None,
        reglas: Reglas | None = None,
    ) -> None:
        self._engine = engine
        self._config = config
        self._cache = cache
        self._reloj = reloj
        self._atajo = atajo
        self._reglas = reglas or cargar_reglas()
        self._cal = config.negocio.calendario_id

    # --- punto de entrada ---
    def procesar(self, entrante: Entrante) -> list[Saliente]:
        """Procesa un mensaje; mensajes repetidos (mismo id de WhatsApp) no tienen efecto."""
        ahora = self._reloj.ahora()
        if not self._registrar(entrante.wa_message_id, ahora):
            return []
        with unidad_de_trabajo(self._engine) as s:
            sesion = cargar_sesion(
                s, entrante.telefono, ahora, self._config.negocio.inactividad_reinicio_minutos
            )
        salidas = self._decidir(entrante, sesion, ahora)
        with unidad_de_trabajo(self._engine) as s:
            guardar_sesion(s, entrante.telefono, sesion, ahora)
        return salidas

    def _registrar(self, wa_message_id: str, ahora: datetime) -> bool:
        try:
            with unidad_de_trabajo(self._engine) as s:
                s.add(MensajeProcesado(wa_message_id=wa_message_id, recibido_en=ahora))
        except IntegrityError:
            return False
        return True

    # --- decisión ---
    def _decidir(self, e: Entrante, sesion: Sesion, ahora: datetime) -> list[Saliente]:
        ctx = sesion.contexto
        if not sesion.aviso_aceptado:
            return self._aviso(e, sesion)
        if e.texto is None and e.id_interactivo is None:
            return [Saliente("texto", texto("no_texto")), *self._menu(ctx)]
        entrada = e.id_interactivo
        if entrada is None and e.texto is not None:
            if ctx.esperando_direccion:
                return self._recibir_direccion(e.texto, ctx, ahora)
            respuesta = self._interpretar(e.texto)
            if isinstance(respuesta, list):
                return respuesta
            entrada = respuesta
        if entrada is None:
            return self._reofrecer(ctx, e, ahora, desconocido=ctx.estado != Estado.INICIO)
        manejador = self._manejador(entrada)
        if manejador is None:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        return manejador(entrada, e, ctx, ahora)

    def _interpretar(self, mensaje: str) -> list[Saliente] | str | None:
        """FAQ y palabras clave primero (sin LLM); si no hay coincidencia, el gancho opcional."""
        faq = resolver_faq(mensaje, self._reglas)
        if faq is not None:
            return [Saliente("texto", texto(faq))]
        intencion = resolver_por_palabras_clave(mensaje, self._reglas)
        if intencion is not None:
            respuestas: dict[str, list[Saliente] | str] = {
                "agendar": "menu:agendar",
                "reagendar": "menu:gestionar",
                "cancelar": "menu:gestionar",
                "saludo": "saludo:hola",
                "consultar_precio": [Saliente("texto", self._texto_servicios())],
                "consultar_horario": [Saliente("texto", self._texto_horario())],
                "consultar_ubicacion": [Saliente("texto", self._texto_ubicacion())],
            }
            return respuestas[intencion]
        return self._atajo(mensaje) if self._atajo is not None else None

    def _manejador(
        self, entrada: str
    ) -> Callable[[str, Entrante, Contexto, datetime], list[Saliente]] | None:
        prefijo = entrada.split(":", 1)[0]
        return {
            "menu": self._on_menu,
            "saludo": self._on_saludo,
            "srv": self._on_servicio,
            "mod": self._on_modalidad,
            "dia": self._on_dia,
            "hora": self._on_hora,
            "conf": self._on_confirmacion,
            "cita": self._on_cita,
            "ger": self._on_gestion,
        }.get(prefijo)

    # --- aviso de datos (EARS-CON-06) ---
    def _aviso(self, e: Entrante, sesion: Sesion) -> list[Saliente]:
        if e.id_interactivo == "aviso:no":
            return [Saliente("texto", texto("aviso_rechazado"))]
        if e.id_interactivo == "aviso:si":
            sesion.aviso_aceptado = True
            return self._bienvenida(e, sesion.contexto)
        cuerpo = texto("aviso_datos", negocio=texto("nombre_negocio"))
        opciones = [
            Opcion("aviso:si", texto("boton_acepto")),
            Opcion("aviso:no", texto("boton_no_acepto")),
        ]
        return [Saliente("botones", cuerpo, opciones)]

    # --- menú ---
    def _a_menu(self, ctx: Contexto) -> None:
        """Lleva el contexto al MENÚ por las transiciones permitidas y limpia la selección."""
        if ctx.estado == Estado.FINALIZADO:
            ctx.ir(Estado.INICIO)
        ctx.limpiar_seleccion()
        ctx.ir(Estado.MENU)

    def _menu(self, ctx: Contexto) -> list[Saliente]:
        self._a_menu(ctx)
        return [Saliente("botones", texto("menu_cuerpo"), _opciones_menu())]

    def _bienvenida(self, e: Entrante, ctx: Contexto) -> list[Saliente]:
        nombre = (
            f" {e.nombre_perfil.split()[0]}" if e.nombre_perfil and e.nombre_perfil.strip() else ""
        )
        self._a_menu(ctx)
        return [
            Saliente("texto", texto("bienvenida", nombre=nombre)),
            Saliente("botones", texto("menu_cuerpo"), _opciones_menu()),
        ]

    def _on_menu(self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        if entrada == "menu:agendar":
            return self._iniciar_agendar(e, ctx, ahora)
        if entrada == "menu:gestionar":
            return self._listar_citas(e, ctx, ahora)
        if entrada == "menu:consultar":
            return [Saliente("texto", self._texto_servicios()), *self._menu(ctx)]
        return self._reofrecer(ctx, e, ahora, desconocido=True)

    def _on_saludo(
        self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime
    ) -> list[Saliente]:
        return self._bienvenida(e, ctx)

    def _texto_horario(self) -> str:
        lineas = []
        for indice, nombre in enumerate(DIAS):
            horario = self._config.negocio.horario.get(nombre)
            if horario is not None:
                lineas.append(
                    texto(
                        "info_horario_linea",
                        dia=texto(f"dia_{indice}").capitalize(),
                        abre=hora_reloj(horario.abre),
                        cierra=hora_reloj(horario.cierra),
                    )
                )
        return texto("info_horario", horario="\n".join(lineas))

    def _texto_ubicacion(self) -> str:
        ubicacion = self._config.negocio.ubicacion
        return texto("info_ubicacion", texto=ubicacion.texto, mapa_url=ubicacion.mapa_url)

    def _texto_servicios(self) -> str:
        lineas = [texto("info_servicios_encabezado")]
        for s in self._config.servicios:
            lineas.append(
                texto(
                    "info_servicio_linea",
                    nombre=s.nombre,
                    precio=precio(s.precio_cop),
                    duracion=s.duracion_min,
                )
            )
        dom = self._config.negocio.domicilio
        if dom.habilitado:
            lineas.append(texto("info_domicilio", recargo=precio(dom.recargo_cop or 0)))
        return "\n".join(lineas)

    # --- agendar ---
    def _iniciar_agendar(self, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        maximo = self._config.negocio.max_citas_futuras_por_clienta
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            futuras = contar_futuras(s, e.telefono, ahora)
        self._a_menu(ctx)
        if futuras >= maximo:  # EARS-CON-07: ofrecer reagendar o cancelar
            return [
                Saliente("texto", texto("limite_citas", maximo=maximo)),
                *self._listar_citas(e, ctx, ahora),
            ]
        ctx.ir(Estado.ELIGIENDO_SERVICIO)
        return [self._lista_servicios()]

    def _lista_servicios(self) -> Saliente:
        filas = [
            Opcion(
                f"srv:{s.id}",
                s.nombre,
                texto("servicio_fila_desc", duracion=s.duracion_min, precio=precio(s.precio_cop)),
            )
            for s in self._config.servicios
        ]
        return Saliente("lista", texto("servicios_cuerpo"), filas, texto("servicios_boton"))

    def _on_servicio(
        self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime
    ) -> list[Saliente]:
        if ctx.estado != Estado.ELIGIENDO_SERVICIO:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        try:
            servicio = self._config.servicio(entrada.partition(":")[2])
        except KeyError:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        ctx.servicio_id = servicio.id
        if self._config.negocio.domicilio.habilitado and servicio.domicilio_permitido:
            ctx.ir(Estado.ELIGIENDO_MODALIDAD)
            return [self._botones_modalidad()]
        ctx.modalidad = "local"
        ctx.ir(Estado.ELIGIENDO_DIA)
        return self._lista_dias(ctx, e, ahora)

    # --- modalidad y domicilio (EARS-CON-04, EARS-DOM-*) ---
    def _botones_modalidad(self) -> Saliente:
        opciones = [
            Opcion("mod:local", texto("modalidad_local")),
            Opcion("mod:domicilio", texto("modalidad_domicilio")),
        ]
        return Saliente("botones", texto("modalidad_cuerpo"), opciones)

    def _on_modalidad(
        self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime
    ) -> list[Saliente]:
        if ctx.estado != Estado.ELIGIENDO_MODALIDAD:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        if entrada == "mod:domicilio":
            ctx.modalidad = "domicilio"
            ctx.esperando_direccion = True
            return [Saliente("texto", texto("direccion_pedir"))]
        ctx.modalidad = "local"
        ctx.ir(Estado.ELIGIENDO_DIA)
        return self._lista_dias(ctx, e, ahora)

    def _recibir_direccion(self, direccion: str, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        zonas = self._config.negocio.domicilio.zona_cobertura
        limpia = " ".join(direccion.split())
        if zonas and not any(z.lower() in limpia.lower() for z in zonas):
            return [
                Saliente("texto", texto("direccion_fuera_zona", zonas=", ".join(zonas))),
                self._botones_modalidad(),
            ]
        ctx.direccion = limpia
        ctx.esperando_direccion = False
        ctx.ir(Estado.ELIGIENDO_DIA)
        return self._lista_dias(ctx, None, ahora)

    # --- día y hora ---
    def _servicio_y_modalidad(self, ctx: Contexto) -> tuple[Servicio, Modalidad]:
        assert ctx.servicio_id is not None  # noqa: S101 - invariante del flujo
        modalidad: Modalidad = "domicilio" if ctx.modalidad == "domicilio" else "local"
        return self._config.servicio(ctx.servicio_id), modalidad

    def _lista_dias(self, ctx: Contexto, e: Entrante | None, ahora: datetime) -> list[Saliente]:
        servicio, modalidad = self._servicio_y_modalidad(ctx)
        dias = dias_con_cupo(self._engine, self._cache, self._config, servicio, modalidad, ahora)
        if not dias:
            return [Saliente("texto", texto("dia_sin_horarios")), *self._menu(ctx)]
        negocio = self._config.negocio
        ctx.dias_ofrecidos = [d.isoformat() for d in dias]
        filas = [
            Opcion(
                f"dia:{d.isoformat()}",
                fecha_corta(_mediodia(d, negocio.zona), negocio),
                fecha(_mediodia(d, negocio.zona), negocio),
            )
            for d in dias
        ]
        return [Saliente("lista", texto("dia_cuerpo"), filas, texto("dia_boton"))]

    def _on_dia(self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        if entrada == "dia:otro" and ctx.estado == Estado.ELIGIENDO_HORA:
            ctx.ir(Estado.ELIGIENDO_DIA)
            return self._lista_dias(ctx, e, ahora)
        valor = entrada.partition(":")[2]
        if ctx.estado != Estado.ELIGIENDO_DIA or valor not in ctx.dias_ofrecidos:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        ctx.fecha = valor
        ctx.ir(Estado.ELIGIENDO_HORA)
        return self._lista_horas(ctx, e, ahora)

    def _lista_horas(
        self, ctx: Contexto, e: Entrante, ahora: datetime, *, previo: str | None = None
    ) -> list[Saliente]:
        assert ctx.fecha is not None  # noqa: S101 - invariante del flujo
        servicio, modalidad = self._servicio_y_modalidad(ctx)
        dia = date.fromisoformat(ctx.fecha)
        horas = horas_libres(
            self._engine, self._cache, self._config, servicio, modalidad, dia, ahora
        )
        negocio = self._config.negocio
        if not horas:
            ctx.ir(Estado.ELIGIENDO_DIA)
            return [Saliente("texto", texto("hora_sin_horarios")), *self._lista_dias(ctx, e, ahora)]
        ctx.horas_ofrecidas = [h.isoformat() for h in horas]
        filas = [Opcion(f"hora:{h.isoformat()}", hora(h, negocio)) for h in horas]
        filas.append(Opcion("dia:otro", texto("otro_dia")))
        cuerpo = texto("hora_cuerpo", dia=fecha(horas[0], negocio))
        salida = Saliente("lista", cuerpo, filas, texto("hora_boton"))
        if previo is not None:
            return [Saliente("texto", previo), salida]
        return [salida]

    def _on_hora(self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        valor = entrada.partition(":")[2]
        # CONFIRMANDO solo se alcanza tocando una hora que el propio sistema ofreció (EARS-CON-03)
        if ctx.estado != Estado.ELIGIENDO_HORA or valor not in ctx.horas_ofrecidas:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        ctx.hora = valor
        ctx.ir(Estado.CONFIRMANDO)
        return [self._resumen(ctx)]

    def _resumen(self, ctx: Contexto) -> Saliente:
        assert ctx.hora is not None  # noqa: S101 - invariante del flujo
        negocio = self._config.negocio
        servicio, modalidad = self._servicio_y_modalidad(ctx)
        inicio = datetime.fromisoformat(ctx.hora)
        comunes = {
            "servicio": servicio.nombre,
            "fecha": fecha(inicio, negocio),
            "hora": hora(inicio, negocio),
        }
        if modalidad == "domicilio":
            recargo = negocio.domicilio.recargo_cop or 0
            cuerpo = texto(
                "resumen_domicilio",
                **comunes,
                direccion=ctx.direccion or "",
                precio=precio(servicio.precio_cop),
                recargo=precio(recargo),
                total=precio(servicio.precio_cop + recargo),
            )
        else:
            cuerpo = texto(
                "resumen_local",
                **comunes,
                ubicacion=negocio.ubicacion.texto,
                total=precio(servicio.precio_cop),
            )
        opciones = [
            Opcion("conf:si", texto("boton_confirmar")),
            Opcion("conf:cambiar", texto("boton_cambiar")),
        ]
        return Saliente("botones", cuerpo, opciones)

    def _on_confirmacion(
        self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime
    ) -> list[Saliente]:
        if ctx.estado != Estado.CONFIRMANDO or ctx.hora is None:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        if entrada == "conf:cambiar":
            ctx.ir(Estado.ELIGIENDO_HORA)
            return self._lista_horas(ctx, e, ahora)
        inicio = datetime.fromisoformat(ctx.hora)
        servicio, modalidad = self._servicio_y_modalidad(ctx)
        try:
            with unidad_de_trabajo(self._engine) as s:
                if ctx.reagendando is not None:
                    nueva = reagendar(s, ctx.reagendando, e.telefono, inicio, self._config, ahora)
                else:
                    solicitud = SolicitudReserva(
                        e.telefono, e.nombre_perfil, servicio.id, inicio, modalidad, ctx.direccion
                    )
                    nueva = reservar(s, solicitud, self._config, ahora)
        except HorarioOcupado:
            ctx.ir(Estado.ELIGIENDO_HORA)
            return self._lista_horas(ctx, e, ahora, previo=texto("horario_ocupado"))
        except LimiteCitas:
            maximo = self._config.negocio.max_citas_futuras_por_clienta
            return [
                Saliente("texto", texto("limite_citas", maximo=maximo)),
                *self._listar_citas(e, ctx, ahora),
            ]
        except (ReservaInvalida, ReservaNoEncontrada):
            log.warning("confirmacion_rechazada", extra={"estado": str(ctx.estado)})
            return [Saliente("texto", texto("error_generico")), *self._menu(ctx)]
        self._cache.invalidar(self._cal, inicio.astimezone(self._config.negocio.zona).date())
        log.info("reserva_apartada", extra={"reserva": nueva.id})
        ctx.ir(Estado.FINALIZADO)
        ctx.limpiar_seleccion()
        return [Saliente("texto", texto("cita_apartada"))]

    # --- mis citas: reagendar y cancelar (EARS-RES-07/08) ---
    def _listar_citas(self, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        if ctx.estado != Estado.MENU:
            self._a_menu(ctx)
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            citas = citas_futuras(s, e.telefono, ahora)  # solo las de ESTA clienta
        if not citas:
            return [Saliente("texto", texto("gestion_sin_citas")), *self._menu(ctx)]
        ctx.ir(Estado.GESTIONANDO_CITA)
        negocio = self._config.negocio
        filas = [
            Opcion(
                f"cita:{c.id}",
                fecha_corta(c.inicio, negocio),
                texto(
                    "gestion_fila_desc",
                    servicio=self._config.servicio(c.servicio_id).nombre,
                    hora=hora(c.inicio, negocio),
                ),
            )
            for c in citas
        ]
        return [Saliente("lista", texto("gestion_elegir"), filas, texto("gestion_boton"))]

    def _cita_propia(self, e: Entrante, reserva_id: int, ahora: datetime) -> Reserva | None:
        with unidad_de_trabajo(self._engine, escritura=False) as s:
            for c in citas_futuras(s, e.telefono, ahora):
                if c.id == reserva_id:
                    return c
        return None

    def _on_cita(self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime) -> list[Saliente]:
        cita = (
            self._cita_desde(entrada, e, ahora) if ctx.estado == Estado.GESTIONANDO_CITA else None
        )
        if cita is None:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        negocio = self._config.negocio
        opciones = [
            Opcion(f"ger:reagendar:{cita.id}", texto("boton_reagendar")),
            Opcion(f"ger:cancelar:{cita.id}", texto("boton_cancelar")),
        ]
        cuerpo = texto(
            "gestion_acciones", fecha=fecha(cita.inicio, negocio), hora=hora(cita.inicio, negocio)
        )
        return [Saliente("botones", cuerpo, opciones)]

    def _cita_desde(self, entrada: str, e: Entrante, ahora: datetime) -> Reserva | None:
        try:
            reserva_id = int(entrada.split(":")[-1])
        except ValueError:
            return None
        return self._cita_propia(e, reserva_id, ahora)

    def _on_gestion(
        self, entrada: str, e: Entrante, ctx: Contexto, ahora: datetime
    ) -> list[Saliente]:
        if ctx.estado != Estado.GESTIONANDO_CITA:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        if entrada == "ger:no":
            return self._menu(ctx)
        accion = entrada.split(":")[1]
        cita = self._cita_desde(entrada, e, ahora)
        if cita is None:
            return self._reofrecer(ctx, e, ahora, desconocido=True)
        negocio = self._config.negocio
        if accion == "cancelar":
            cuerpo = texto(
                "cancelar_confirmar",
                fecha=fecha(cita.inicio, negocio),
                hora=hora(cita.inicio, negocio),
            )
            opciones = [
                Opcion(f"ger:si:{cita.id}", texto("boton_si_cancelar")),
                Opcion("ger:no", texto("boton_no_volver")),
            ]
            return [Saliente("botones", cuerpo, opciones)]
        if accion == "si":
            return self._cancelar(e, ctx, cita, ahora)
        if accion == "reagendar":
            ctx.reagendando = cita.id
            ctx.servicio_id = cita.servicio_id
            ctx.modalidad = cita.modalidad
            ctx.direccion = cita.direccion
            ctx.ir(Estado.ELIGIENDO_DIA)
            return self._lista_dias(ctx, e, ahora)
        return self._reofrecer(ctx, e, ahora, desconocido=True)

    def _cancelar(
        self, e: Entrante, ctx: Contexto, cita: Reserva, ahora: datetime
    ) -> list[Saliente]:
        try:
            with unidad_de_trabajo(self._engine) as s:
                cancelar(s, cita.id, e.telefono, ahora)
        except (ReservaInvalida, ReservaNoEncontrada):
            return [Saliente("texto", texto("error_generico")), *self._menu(ctx)]
        dia = cita.inicio.astimezone(self._config.negocio.zona).date()
        self._cache.invalidar(self._cal, dia)
        ctx.ir(Estado.FINALIZADO)
        ctx.limpiar_seleccion()
        return [
            Saliente(
                "texto", texto("cancelacion_ok", fecha=fecha(cita.inicio, self._config.negocio))
            )
        ]

    # --- reofrecer la pantalla del estado actual ---
    def _reofrecer(
        self, ctx: Contexto, e: Entrante, ahora: datetime, *, desconocido: bool
    ) -> list[Saliente]:
        previo = [Saliente("texto", texto("no_entendi"))] if desconocido else []
        estado = ctx.estado
        if estado in (Estado.INICIO, Estado.FINALIZADO):
            return self._bienvenida(e, ctx)
        if estado == Estado.MENU:
            return [*previo, *self._menu(ctx)]
        if estado == Estado.ELIGIENDO_SERVICIO:
            return [*previo, self._lista_servicios()]
        if estado == Estado.ELIGIENDO_MODALIDAD:
            if ctx.esperando_direccion:
                return [*previo, Saliente("texto", texto("direccion_pedir"))]
            return [*previo, self._botones_modalidad()]
        if estado == Estado.ELIGIENDO_DIA:
            return [*previo, *self._lista_dias(ctx, e, ahora)]
        if estado == Estado.ELIGIENDO_HORA:
            return [*previo, *self._lista_horas(ctx, e, ahora)]
        if estado == Estado.CONFIRMANDO:
            return [*previo, self._resumen(ctx)]
        return [*previo, *self._listar_citas(e, ctx, ahora)]  # GESTIONANDO_CITA


def _mediodia(d: date, zona: ZoneInfo) -> datetime:
    """Instante a mediodía local (evita bordes de zona al formatear un día)."""
    return datetime.combine(d, time(12, 0), tzinfo=zona)
