"""Modelos ORM (design.md §2), engine SQLite con WAL y escritura serializada (§1.3)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Engine,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    create_engine,
    event,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

FORMATO_FECHA = "%Y-%m-%dT%H:%M:%SZ"


class FechaUTC(TypeDecorator[datetime]):
    """Guarda datetime con zona como texto ISO-8601 UTC ('2026-11-03T15:00:00Z')."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("la fecha debe tener zona horaria")
        return value.astimezone(UTC).strftime(FORMATO_FECHA)

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return datetime.strptime(value, FORMATO_FECHA).replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Conversacion(Base):
    __tablename__ = "conversaciones"
    telefono: Mapped[str] = mapped_column(String, primary_key=True)
    estado: Mapped[str] = mapped_column(String)
    contexto: Mapped[str] = mapped_column(String, default="{}", server_default="{}")
    aviso_datos_aceptado: Mapped[bool] = mapped_column(default=False, server_default="0")
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC)


class MensajeProcesado(Base):
    __tablename__ = "mensajes_procesados"
    wa_message_id: Mapped[str] = mapped_column(String, primary_key=True)
    recibido_en: Mapped[datetime] = mapped_column(FechaUTC)


class Reserva(Base):
    __tablename__ = "reservas"
    __table_args__ = (
        CheckConstraint("modalidad IN ('local','domicilio')", name="ck_reservas_modalidad"),
        CheckConstraint(
            "estado IN ('PENDIENTE','CONFIRMADA','CANCELANDO','CANCELADA','EXPIRADA','FALLIDA')",
            name="ck_reservas_estado",
        ),
        Index("ix_reservas_tel_estado", "telefono", "estado", "inicio"),
        Index("ix_reservas_exp", "estado", "expira_en"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calendario_id: Mapped[str] = mapped_column(String)
    telefono: Mapped[str] = mapped_column(String)
    nombre_perfil: Mapped[str | None] = mapped_column(String, default=None)
    servicio_id: Mapped[str] = mapped_column(String)
    modalidad: Mapped[str] = mapped_column(String, default="local", server_default="local")
    direccion: Mapped[str | None] = mapped_column(String, default=None)
    precio_cop: Mapped[int] = mapped_column(Integer)
    recargo_cop: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    inicio: Mapped[datetime] = mapped_column(FechaUTC)
    fin: Mapped[datetime] = mapped_column(FechaUTC)
    estado: Mapped[str] = mapped_column(String)
    event_id: Mapped[str | None] = mapped_column(String, default=None)
    expira_en: Mapped[datetime | None] = mapped_column(FechaUTC, default=None)
    reemplaza_a: Mapped[int | None] = mapped_column(ForeignKey("reservas.id"), default=None)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC)


class ReservaBloque(Base):
    __tablename__ = "reserva_bloques"
    calendario_id: Mapped[str] = mapped_column(String, primary_key=True)
    bloque_inicio: Mapped[datetime] = mapped_column(FechaUTC, primary_key=True)
    reserva_id: Mapped[int] = mapped_column(ForeignKey("reservas.id"))


class ColaEscritura(Base):
    __tablename__ = "cola_escritura"
    __table_args__ = (
        CheckConstraint(
            "tipo_operacion IN ('CREAR','ACTUALIZAR','ELIMINAR','REAGENDAR')",
            name="ck_cola_tipo",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE','PROCESANDO','COMPLETADA','FALLIDA')", name="ck_cola_estado"
        ),
        Index("ix_cola_pend", "calendario_id", "estado", "proxima_ejecucion", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calendario_id: Mapped[str] = mapped_column(String)
    tipo_operacion: Mapped[str] = mapped_column(String)
    payload: Mapped[str] = mapped_column(String)
    estado: Mapped[str] = mapped_column(String, default="PENDIENTE", server_default="PENDIENTE")
    intentos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    proxima_ejecucion: Mapped[datetime] = mapped_column(FechaUTC)
    clave_idempotencia: Mapped[str] = mapped_column(String, unique=True)
    reserva_id: Mapped[int | None] = mapped_column(ForeignKey("reservas.id"), default=None)
    resultado: Mapped[str | None] = mapped_column(String, default=None)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC)


class CacheDisponibilidadFila(Base):
    __tablename__ = "cache_disponibilidad"
    calendario_id: Mapped[str] = mapped_column(String, primary_key=True)
    fecha: Mapped[str] = mapped_column(String, primary_key=True)
    bloques_ocupados: Mapped[str] = mapped_column(String)
    generado_en: Mapped[datetime] = mapped_column(FechaUTC)
    valido_hasta: Mapped[datetime] = mapped_column(FechaUTC)


class CalendarioEstado(Base):
    __tablename__ = "calendario_estado"
    calendario_id: Mapped[str] = mapped_column(String, primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String, default=None)
    canal_id: Mapped[str | None] = mapped_column(String, default=None)
    canal_resource_id: Mapped[str | None] = mapped_column(String, default=None)
    canal_expira_en: Mapped[datetime | None] = mapped_column(FechaUTC, default=None)
    modo_degradado: Mapped[bool] = mapped_column(default=True, server_default="1")
    cola_pausada_hasta: Mapped[datetime | None] = mapped_column(FechaUTC, default=None)


class EventoEspejo(Base):
    __tablename__ = "eventos_espejo"
    calendario_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    inicio: Mapped[datetime] = mapped_column(FechaUTC)
    fin: Mapped[datetime] = mapped_column(FechaUTC)
    estado: Mapped[str] = mapped_column(String)


class Metrica(Base):
    __tablename__ = "metricas"
    nombre: Mapped[str] = mapped_column(String, primary_key=True)
    valor: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


def crear_engine(url: str, *, busy_timeout_ms: int = 5000) -> Engine:
    """Engine SQLite con PRAGMAs de §1.3; BEGIN se emite a mano para poder usar IMMEDIATE."""
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _al_conectar(dbapi_conexion: Any, _registro: Any) -> None:
        dbapi_conexion.isolation_level = None  # el driver no emite BEGIN por su cuenta
        cursor = dbapi_conexion.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _al_comenzar(conexion: Connection) -> None:
        inmediata = conexion.get_execution_options().get("inmediata", False)
        conexion.exec_driver_sql("BEGIN IMMEDIATE" if inmediata else "BEGIN")

    return engine


def crear_tablas(engine: Engine) -> None:
    """Crea las tablas si no existen."""
    Base.metadata.create_all(engine)


@contextmanager
def unidad_de_trabajo(engine: Engine, *, escritura: bool = True) -> Iterator[Session]:
    """Sesión por unidad de trabajo: commit al salir, rollback ante excepción.

    Las escrituras abren la transacción con BEGIN IMMEDIATE (una sola escritora a la vez).
    """
    enlazado = engine.execution_options(inmediata=True) if escritura else engine
    sesion = sessionmaker(enlazado, expire_on_commit=False)()
    try:
        yield sesion
        sesion.commit()
    except BaseException:
        sesion.rollback()
        raise
    finally:
        sesion.close()
