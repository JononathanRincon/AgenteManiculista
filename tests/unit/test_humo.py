"""Prueba de humo: el paquete se importa y el entorno es Python 3.12."""

import sys

import app


def test_paquete_importable() -> None:
    assert app.__doc__ is not None


def test_version_de_python() -> None:
    assert sys.version_info[:2] == (3, 12)
