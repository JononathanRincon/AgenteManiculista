"""Humanización de envíos: tiempos dentro de rango y secuencia estricta (EARS-WA-06..09)."""

import pytest
from app.config import AntiBan
from app.humanizacion import SecuenciaEnvio, sin_espera, tiempo_escritura, tiempo_lectura

CFG = AntiBan()
TEL = "573001112233"


def _minimo(a: float, b: float) -> float:
    return a


def _maximo(a: float, b: float) -> float:
    return b


def _medio(a: float, b: float) -> float:
    return (a + b) / 2


def test_tiempo_de_lectura_entre_1_5_y_3_segundos() -> None:
    assert tiempo_lectura(CFG, _minimo) == 1.5
    assert tiempo_lectura(CFG, _maximo) == 3.0
    assert tiempo_lectura(CFG, _medio) == 2.25
    for _ in range(200):  # con el azar real siempre cae en el rango
        assert 1.5 <= tiempo_lectura(CFG) <= 3.0


def test_tiempo_de_escritura_proporcional_con_minimo_y_maximo() -> None:
    corto, medio, largo = "ok", "x" * 80, "x" * 400
    # sin jitter (punto medio de [-400, 600] es +100 → comparamos con jitter explícito)
    sin_jitter = AntiBan(jitter_min_ms=0, jitter_max_ms=0)
    assert tiempo_escritura(corto, sin_jitter, _minimo) == 2.5  # mínimo
    assert tiempo_escritura(medio, sin_jitter, _minimo) == pytest.approx(3.6)  # 80 × 45 ms
    assert tiempo_escritura(largo, sin_jitter, _minimo) == 6.0  # máximo


def test_el_jitter_se_suma_entre_menos_400_y_mas_600_ms() -> None:
    assert tiempo_escritura("ok", CFG, _minimo) == pytest.approx(2.1)
    assert tiempo_escritura("ok", CFG, _maximo) == pytest.approx(3.1)
    assert tiempo_escritura("x" * 400, CFG, _minimo) == pytest.approx(5.6)
    assert tiempo_escritura("x" * 400, CFG, _maximo) == pytest.approx(6.6)
    for _ in range(300):
        assert 2.1 <= tiempo_escritura("ok", CFG) <= 3.1


def test_nunca_hay_tiempos_negativos() -> None:
    raro = AntiBan(escritura_min_ms=0, escritura_max_ms=0, jitter_min_ms=-900, jitter_max_ms=-500)
    assert tiempo_escritura("", raro, _minimo) == 0.0


def test_ninguna_respuesta_sale_en_menos_de_2_5_segundos_tras_el_mensaje() -> None:
    # lectura mínima + escritura mínima con el peor jitter
    assert tiempo_lectura(CFG, _minimo) + tiempo_escritura("", CFG, _minimo) >= 2.5


class SidecarRegistro:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, ...]] = []

    def leer(self, telefono: str, id_mensaje: str) -> None:
        self.eventos.append(("leer", telefono, id_mensaje))

    def presencia(self, telefono: str, estado: str) -> None:
        self.eventos.append(("presencia", telefono, estado))

    def enviar(self, telefono: str, texto: str) -> str:
        self.eventos.append(("enviar", telefono, texto))
        return "ID-1"


def test_secuencia_leer_composing_espera_paused_y_un_solo_envio() -> None:
    sidecar = SidecarRegistro()
    esperas: list[float] = []

    def dormir(segundos: float) -> None:
        esperas.append(segundos)
        sidecar.eventos.append(("espera", f"{segundos:.2f}"))

    secuencia = SecuenciaEnvio(sidecar, CFG, dormir, _minimo)
    identificador = secuencia.enviar_respuesta(TEL, "Hola, ¿en qué te ayudo?", "wamid.IN")
    assert identificador == "ID-1"
    assert [e[0] if e[0] != "presencia" else e[2] for e in sidecar.eventos] == [
        "espera",
        "leer",
        "composing",
        "espera",
        "paused",
        "enviar",
    ]
    assert sum(1 for e in sidecar.eventos if e[0] == "enviar") == 1
    assert esperas[0] == 1.5
    assert esperas[1] == pytest.approx(tiempo_escritura("Hola, ¿en qué te ayudo?", CFG, _minimo))


def test_sin_mensaje_entrante_no_hay_pausa_de_lectura() -> None:
    sidecar = SidecarRegistro()
    esperas: list[float] = []
    SecuenciaEnvio(sidecar, CFG, esperas.append, _minimo).enviar_respuesta(TEL, "segundo mensaje")
    assert [e[0] for e in sidecar.eventos] == ["presencia", "presencia", "enviar"]
    assert len(esperas) == 1  # solo la espera de escritura


def test_sin_espera_no_duerme_de_verdad() -> None:
    sidecar = SidecarRegistro()
    SecuenciaEnvio(sidecar, CFG, sin_espera).enviar_respuesta(TEL, "x" * 300, "wamid.IN")
    assert [e[0] for e in sidecar.eventos] == ["leer", "presencia", "presencia", "enviar"]


def test_si_la_espera_falla_la_presencia_igual_se_cierra() -> None:
    sidecar = SidecarRegistro()
    llamadas = {"n": 0}

    def dormir(segundos: float) -> None:
        llamadas["n"] += 1
        if llamadas["n"] == 2:  # la espera de escritura
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        SecuenciaEnvio(sidecar, CFG, dormir, _minimo).enviar_respuesta(TEL, "hola", "wamid.IN")
    estados = [e[2] for e in sidecar.eventos if e[0] == "presencia"]
    assert estados == ["composing", "paused"]
    assert not any(e[0] == "enviar" for e in sidecar.eventos)
