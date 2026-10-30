"""Debounce por teléfono: tres mensajes seguidos producen una sola respuesta (EARS-WA-16, EC-21)."""

import asyncio
from datetime import UTC, datetime

from app.acumulador import Acumulador
from app.conversacion import Entrante

TS = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _msg(
    texto: str | None, n: int, tel: str = "573001112233", boton: str | None = None
) -> Entrante:
    return Entrante(tel, "Ana", f"wamid.{tel}.{n}", texto, boton, TS)


class Registro:
    def __init__(self, falla_en: int | None = None, demora_s: float = 0.0) -> None:
        self.llamadas: list[Entrante] = []
        self.falla_en = falla_en
        self.demora_s = demora_s

    async def __call__(self, entrante: Entrante) -> None:
        self.llamadas.append(entrante)
        if self.demora_s:
            await asyncio.sleep(self.demora_s)
        if self.falla_en == len(self.llamadas):
            raise RuntimeError("falla del procesamiento")


async def test_ventana_cero_procesa_cada_mensaje_al_instante() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 0)
    await acumulador.recibir(_msg("uno", 1))
    await acumulador.recibir(_msg("dos", 2))
    assert [e.texto for e in registro.llamadas] == ["uno", "dos"]


async def test_tres_mensajes_en_la_ventana_se_unen_en_una_sola_invocacion() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 0.05)
    for n, texto in enumerate(("Hola", "¿Tienes cita?", "Para mañana"), start=1):
        await acumulador.recibir(_msg(texto, n))
    assert registro.llamadas == []  # todavía espera
    assert acumulador.pendientes() == 1
    await asyncio.sleep(0.15)
    assert len(registro.llamadas) == 1
    unido = registro.llamadas[0]
    assert unido.texto == "Hola\n¿Tienes cita?\nPara mañana"
    assert unido.wa_message_id == "wamid.573001112233.3"  # el del último mensaje
    assert acumulador.pendientes() == 0


async def test_cada_mensaje_nuevo_reinicia_la_ventana() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 0.08)
    await acumulador.recibir(_msg("uno", 1))
    await asyncio.sleep(0.05)
    await acumulador.recibir(_msg("dos", 2))
    await asyncio.sleep(0.05)  # 0,10 s desde el primero pero solo 0,05 s desde el último
    assert registro.llamadas == []
    await asyncio.sleep(0.12)
    assert [e.texto for e in registro.llamadas] == ["uno\ndos"]


async def test_telefonos_distintos_no_se_mezclan() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 0.03)
    await acumulador.recibir(_msg("de ana", 1, "573001111111"))
    await acumulador.recibir(_msg("de luisa", 1, "573002222222"))
    await asyncio.sleep(0.12)
    assert sorted((e.telefono[-4:], e.texto) for e in registro.llamadas) == [
        ("1111", "de ana"),
        ("2222", "de luisa"),
    ]


async def test_un_toque_de_boton_vacia_lo_pendiente_y_se_procesa_aparte_y_en_orden() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 5)
    await acumulador.recibir(_msg("hola", 1))
    await acumulador.recibir(_msg(None, 2, boton="menu:agendar"))
    assert [(e.texto, e.id_interactivo) for e in registro.llamadas] == [
        ("hola", None),
        (None, "menu:agendar"),
    ]
    assert acumulador.pendientes() == 0
    await acumulador.cerrar()


async def test_un_mensaje_no_textual_tambien_se_procesa_aparte() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 5)
    await acumulador.recibir(_msg(None, 1))
    assert len(registro.llamadas) == 1


async def test_los_textos_en_blanco_no_generan_invocaciones() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 0.03)
    await acumulador.recibir(_msg("   ", 1))
    await acumulador.recibir(_msg("", 2))
    await asyncio.sleep(0.1)
    assert registro.llamadas == []


async def test_un_fallo_del_procesamiento_no_rompe_los_siguientes_mensajes() -> None:
    registro = Registro(falla_en=1)
    acumulador = Acumulador(registro, 0.03)
    await acumulador.recibir(_msg("primero", 1))
    await asyncio.sleep(0.1)
    await acumulador.recibir(_msg("segundo", 2))
    await asyncio.sleep(0.1)
    assert [e.texto for e in registro.llamadas] == ["primero", "segundo"]


async def test_un_mensaje_que_llega_mientras_se_procesa_no_cancela_el_proceso_en_marcha() -> None:
    registro = Registro(demora_s=0.15)
    acumulador = Acumulador(registro, 0.03)
    await acumulador.recibir(_msg("uno", 1))
    await asyncio.sleep(0.08)  # ya venció la ventana y el procesamiento está en marcha
    await acumulador.recibir(_msg("dos", 2))
    await asyncio.sleep(0.5)
    assert [e.texto for e in registro.llamadas] == ["uno", "dos"]  # dos lotes, en orden


async def test_cerrar_procesa_de_inmediato_lo_que_esperaba() -> None:
    registro = Registro()
    acumulador = Acumulador(registro, 30)
    await acumulador.recibir(_msg("a", 1))
    await acumulador.recibir(_msg("b", 2))
    await acumulador.cerrar()
    assert [e.texto for e in registro.llamadas] == ["a\nb"]
    await acumulador.cerrar()  # idempotente
    assert len(registro.llamadas) == 1
