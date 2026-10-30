"""Red de seguridad local: ningún archivo versionado contiene secretos con forma conocida."""

import re
import subprocess  # noqa: S404 - solo para listar archivos versionados con git
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ESTE_ARCHIVO = Path(__file__).resolve()

PATRONES = {
    "clave de API de Google": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "clave privada": re.compile("-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + " KEY-----"),
    "token de acceso de Meta": re.compile(r"EAA[A-Za-z0-9]{60,}"),
    "clave estilo sk-": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
}
EXTENSIONES_PROHIBIDAS = {".pem", ".key"}


def _es_archivo_de_secretos(ruta: Path) -> bool:
    es_env = ruta.name == ".env" or ruta.name.startswith(".env.")
    return (es_env and ruta.name != ".env.example") or ruta.suffix in EXTENSIONES_PROHIBIDAS


def _archivos_versionados() -> list[Path]:
    salida = subprocess.run(  # noqa: S603 - argumentos fijos, sin entrada externa
        ["git", "ls-files", "-z"],  # noqa: S607 - git debe estar en el PATH
        cwd=RAIZ,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [RAIZ / ruta for ruta in salida.split("\0") if ruta]


def test_no_hay_archivos_de_secretos_versionados() -> None:
    sospechosos = [ruta.name for ruta in _archivos_versionados() if _es_archivo_de_secretos(ruta)]
    assert sospechosos == []


def test_no_hay_secretos_con_forma_conocida() -> None:
    hallazgos: list[str] = []
    for ruta in _archivos_versionados():
        if ruta == ESTE_ARCHIVO or not ruta.is_file() or ruta.name == "uv.lock":
            continue
        try:
            texto = ruta.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hallazgos.extend(
            f"{ruta.relative_to(RAIZ)}: {nombre}"
            for nombre, patron in PATRONES.items()
            if patron.search(texto)
        )
    assert hallazgos == []
