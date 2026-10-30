"""Catálogo de mensajes: todo texto visible sale de config/mensajes.yaml (NFR-MNT-01)."""

from __future__ import annotations

import string
from pathlib import Path

from app.config import ConfiguracionInvalida, leer_yaml

RUTA_POR_DEFECTO = Path(__file__).resolve().parents[1] / "config" / "mensajes.yaml"

_CLAVES_FORMATO = (
    [f"dia_{i}" for i in range(7)]
    + [f"dia_abrev_{i}" for i in range(7)]
    + [f"mes_{i}" for i in range(1, 13)]
    + [f"mes_abrev_{i}" for i in range(1, 13)]
)

CLAVES_REQUERIDAS: frozenset[str] = frozenset(
    {
        "aviso_datos",
        "boton_acepto",
        "boton_no_acepto",
        "aviso_rechazado",
        "bienvenida",
        "menu_cuerpo",
        "menu_agendar",
        "menu_consultar",
        "menu_gestionar",
        "no_entendi",
        "no_texto",
        "error_generico",
        "servicios_cuerpo",
        "servicios_boton",
        "servicio_fila_desc",
        "modalidad_cuerpo",
        "modalidad_local",
        "modalidad_domicilio",
        "direccion_pedir",
        "direccion_fuera_zona",
        "dia_cuerpo",
        "dia_boton",
        "dia_sin_horarios",
        "hora_cuerpo",
        "hora_boton",
        "hora_sin_horarios",
        "resumen_local",
        "resumen_domicilio",
        "boton_confirmar",
        "boton_cambiar",
        "cita_apartada",
        "cita_confirmada",
        "cita_confirmada_domicilio",
        "horario_ocupado",
        "cita_fallida",
        "cita_expirada",
        "limite_citas",
        "gestion_sin_citas",
        "gestion_elegir",
        "gestion_boton",
        "gestion_fila_desc",
        "gestion_acciones",
        "boton_reagendar",
        "boton_cancelar",
        "cancelar_confirmar",
        "boton_si_cancelar",
        "boton_no_volver",
        "cancelacion_ok",
        "reagendar_ok",
        "reagendar_fallo",
        "info_servicios_encabezado",
        "info_servicio_linea",
        "info_domicilio",
        "info_horario",
        "info_horario_linea",
        "info_ubicacion",
        "faq_pagos",
        "faq_demora",
        "precio_formato",
        "hora_am",
        "hora_pm",
        "evento_titulo",
        "evento_descripcion",
        "evento_extra_domicilio",
        "nombre_negocio",
        *_CLAVES_FORMATO,
    }
)


class Mensajes:
    """Catálogo validado de mensajes."""

    def __init__(self, textos: dict[str, str]) -> None:
        self._textos = dict(textos)

    def texto(self, clave: str, **variables: object) -> str:
        """Devuelve el mensaje con sus variables; KeyError si la clave o variable no existe."""
        return self._textos[clave].format(**variables)

    def existe(self, clave: str) -> bool:
        """Indica si la clave está en el catálogo."""
        return clave in self._textos


def cargar_mensajes(ruta: Path = RUTA_POR_DEFECTO) -> Mensajes:
    """Carga mensajes.yaml y falla si falta una clave requerida o una plantilla es inválida."""
    datos = leer_yaml(ruta)
    if not isinstance(datos, dict):
        raise ConfiguracionInvalida(f"{ruta.name}: la raíz debe ser un mapa")
    faltan = sorted(CLAVES_REQUERIDAS - set(datos))
    if faltan:
        raise ConfiguracionInvalida(f"{ruta.name}: faltan las claves {faltan}")
    textos = {str(k): str(v) for k, v in datos.items()}
    for clave, plantilla in textos.items():
        try:
            list(string.Formatter().parse(plantilla))
        except ValueError as exc:
            detalle = f"{ruta.name}: {clave}: plantilla inválida ({exc})"
            raise ConfiguracionInvalida(detalle) from exc
    return Mensajes(textos)


_catalogo: Mensajes | None = None


def configurar(mensajes: Mensajes | None) -> None:
    """Fija (o limpia) el catálogo global; se llama al arrancar y en los tests."""
    global _catalogo  # noqa: PLW0603 - catálogo único de proceso, inmutable tras el arranque
    _catalogo = mensajes


def texto(clave: str, **variables: object) -> str:
    """Mensaje visible por clave (design.md §1.2); carga el catálogo por defecto si hace falta."""
    catalogo = _catalogo
    if catalogo is None:
        catalogo = cargar_mensajes()
        configurar(catalogo)
    return catalogo.texto(clave, **variables)
