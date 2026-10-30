"""Configuración inmutable del negocio, validada al arrancar (EARS-CFG-01/02, EARS-DOM-05)."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Modalidad = Literal["local", "domicilio"]
DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


class ConfiguracionInvalida(Exception):  # noqa: N818 - nombre fijado por el diseño (design.md §B1.2)
    """La configuración no es válida; el mensaje indica archivo y campo."""


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Horario(_Modelo):
    abre: time
    cierra: time

    @model_validator(mode="after")
    def _cierra_despues_de_abrir(self) -> Self:
        if self.cierra <= self.abre:
            raise ValueError("cierra debe ser posterior a abre")
        return self


class Domicilio(_Modelo):
    habilitado: bool = False
    minutos_desplazamiento: int = Field(30, ge=0)
    recargo_cop: int | None = Field(None, ge=0)
    zona_cobertura: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recargo_obligatorio(self) -> Self:
        if self.habilitado and self.recargo_cop is None:
            raise ValueError("recargo_cop es obligatorio cuando el domicilio está habilitado")
        return self


class AntiBan(_Modelo):
    """Controles anti-bloqueo del transporte de WhatsApp (ADR-009)."""

    debounce_segundos: Annotated[float, Field(ge=0, le=30)] = 4.5
    lectura_min_ms: Annotated[int, Field(ge=0)] = 1500
    lectura_max_ms: Annotated[int, Field(ge=0)] = 3000
    escritura_ms_por_caracter: Annotated[int, Field(ge=0)] = 45
    escritura_min_ms: Annotated[int, Field(ge=0)] = 2500
    escritura_max_ms: Annotated[int, Field(ge=0)] = 6000
    jitter_min_ms: int = -400
    jitter_max_ms: int = 600
    max_mensajes_por_telefono: Annotated[int, Field(gt=0)] = 15
    ventana_limite_minutos: Annotated[int, Field(gt=0)] = 10
    max_mensajes_globales: Annotated[int, Field(gt=0)] = 120

    @model_validator(mode="after")
    def _rangos_coherentes(self) -> Self:
        pares = (
            ("lectura", self.lectura_min_ms, self.lectura_max_ms),
            ("escritura", self.escritura_min_ms, self.escritura_max_ms),
            ("jitter", self.jitter_min_ms, self.jitter_max_ms),
        )
        for nombre, minimo, maximo in pares:
            if minimo > maximo:
                raise ValueError(f"el mínimo de {nombre} no puede superar al máximo")
        if self.max_mensajes_globales < self.max_mensajes_por_telefono:
            raise ValueError("el tope global no puede ser menor que el de un teléfono")
        return self


class Ubicacion(_Modelo):
    texto: str
    mapa_url: str


class Negocio(_Modelo):
    calendario_id: str
    zona_horaria: str
    bloque_minutos: int = Field(gt=0)
    margen_minutos: int = Field(ge=0)
    anticipacion_minima_horas: int = Field(ge=0)
    ventana_maxima_dias: int = Field(gt=0)
    max_citas_futuras_por_clienta: int = Field(gt=0)
    cancelacion_minima_horas: int = Field(ge=0)
    cache_ttl_segundos: int = Field(gt=0)
    refresco_degradado_minutos: int = Field(gt=0)
    reserva_pendiente_expira_minutos: int = Field(gt=0)
    inactividad_reinicio_minutos: int = Field(gt=0)
    pausa_intervencion_humana_minutos: int = Field(120, gt=0)
    anti_ban: AntiBan = Field(default_factory=lambda: AntiBan())
    llm_max_por_telefono_hora: int = Field(10, gt=0)
    llm_max_global_dia: int = Field(500, gt=0)
    horario: dict[str, Horario | None]
    domicilio: Domicilio
    ubicacion: Ubicacion

    @field_validator("bloque_minutos")
    @classmethod
    def _divide_la_hora(cls, valor: int) -> int:
        if 60 % valor:
            raise ValueError("bloque_minutos debe dividir 60")
        return valor

    @field_validator("zona_horaria")
    @classmethod
    def _zona_existe(cls, valor: str) -> str:
        try:
            ZoneInfo(valor)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"zona horaria desconocida: {valor}") from exc
        return valor

    @field_validator("horario")
    @classmethod
    def _dias_validos(cls, valor: dict[str, Horario | None]) -> dict[str, Horario | None]:
        desconocidos = set(valor) - set(DIAS)
        if desconocidos:
            raise ValueError(f"días desconocidos: {sorted(desconocidos)}")
        return valor

    @property
    def zona(self) -> ZoneInfo:
        """Zona horaria del negocio."""
        return ZoneInfo(self.zona_horaria)


class Servicio(_Modelo):
    id: str
    nombre: str
    duracion_min: int = Field(gt=0)
    precio_cop: int = Field(ge=0)
    descripcion: str
    domicilio_permitido: bool = False

    @field_validator("duracion_min")
    @classmethod
    def _multiplo_de_cinco(cls, valor: int) -> int:
        if valor % 5:
            raise ValueError("duracion_min debe ser múltiplo de 5")
        return valor


class Config(_Modelo):
    negocio: Negocio
    servicios: tuple[Servicio, ...]

    def servicio(self, servicio_id: str) -> Servicio:
        """Devuelve el servicio por id; KeyError si no existe."""
        for s in self.servicios:
            if s.id == servicio_id:
                return s
        raise KeyError(servicio_id)


def leer_yaml(ruta: Path) -> Any:
    """Lee un YAML; cualquier problema se reporta como ConfiguracionInvalida con el archivo."""
    try:
        return yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfiguracionInvalida(f"{ruta.name}: no se pudo leer ({exc.strerror})") from exc
    except yaml.YAMLError as exc:
        raise ConfiguracionInvalida(f"{ruta.name}: YAML mal formado ({exc})") from exc


def _mapa(ruta: Path) -> dict[str, Any]:
    datos = leer_yaml(ruta)
    if not isinstance(datos, dict):
        raise ConfiguracionInvalida(f"{ruta.name}: la raíz debe ser un mapa")
    return datos


def _describir(archivo: str, exc: ValidationError) -> str:
    partes = [f"{archivo}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return "; ".join(partes)


def cargar_config(directorio: Path) -> Config:
    """Carga y valida negocio.yaml y servicios.yaml; falla con un mensaje claro."""
    negocio_raw = _mapa(directorio / "negocio.yaml")
    servicios_raw = _mapa(directorio / "servicios.yaml")
    try:
        negocio = Negocio.model_validate(negocio_raw)
    except ValidationError as exc:
        raise ConfiguracionInvalida(_describir("negocio.yaml", exc)) from exc
    lista = servicios_raw.get("servicios")
    if not isinstance(lista, list):
        raise ConfiguracionInvalida("servicios.yaml: 'servicios' debe ser una lista")
    try:
        servicios = tuple(Servicio.model_validate(s) for s in lista)
    except ValidationError as exc:
        raise ConfiguracionInvalida(_describir("servicios.yaml", exc)) from exc
    ids = [s.id for s in servicios]
    if len(ids) != len(set(ids)):
        raise ConfiguracionInvalida("servicios.yaml: ids de servicio duplicados")
    return Config(negocio=negocio, servicios=servicios)


class Settings(BaseSettings):
    """Variables de entorno validadas; los valores por defecto son seguros (LLM apagado)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", frozen=True
    )

    entorno: Literal["desarrollo", "demo", "produccion"] = "desarrollo"
    simulador: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str = "sqlite:///./agenda.db"
    proveedor_llm: Literal["gemini", "none"] = "none"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_tier: Literal["free", "paid"] = "free"
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_verify_token: SecretStr | None = None
    google_service_account_json: SecretStr | None = None
    calendar_webhook_url: str | None = None
    calendar_channel_token: SecretStr | None = None
    demo_retardo_calendario_s: Annotated[float, Field(ge=0, le=5)] = 0.0

    @model_validator(mode="after")
    def _gemini_requiere_clave(self) -> Self:
        if self.proveedor_llm == "gemini" and self.gemini_api_key is None:
            raise ValueError("GEMINI_API_KEY es obligatoria cuando PROVEEDOR_LLM=gemini")
        return self

    @model_validator(mode="after")
    def _produccion_exige_tier_de_pago(self) -> Self:
        # el tier gratuito puede usar el contenido para mejorar productos de Google (Ley 1581)
        usa_gemini_en_produccion = self.entorno == "produccion" and self.proveedor_llm == "gemini"
        if usa_gemini_en_produccion and self.gemini_tier != "paid":
            raise ValueError("en producción GEMINI_TIER debe ser paid cuando se usa Gemini")
        return self
