"""Infraestructura del sidecar: no se expone, corre sin root y su sesión nunca se versiona."""

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parents[2]


def _compose() -> dict[str, Any]:
    datos = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(datos, dict)
    return datos


def test_el_sidecar_no_publica_puertos_y_guarda_la_sesion_en_un_volumen() -> None:
    sidecar = _compose()["services"]["sidecar"]
    assert "ports" not in sidecar  # solo accesible desde la red de compose
    assert sidecar["expose"] == ["3000"]
    assert "baileys_auth:/data/baileys_auth" in sidecar["volumes"]
    assert "baileys_auth" in _compose()["volumes"]
    assert sidecar["profiles"] == ["whatsapp"]


def test_los_secretos_no_estan_escritos_en_el_compose() -> None:
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    for nombre in ("SIDECAR_TOKEN", "WEBHOOK_SECRET", "ADMIN_TOKEN"):
        assert f"{nombre}:" not in texto  # llegan por el archivo .env, nunca versionado


def test_el_backend_apunta_al_sidecar_por_la_red_interna() -> None:
    entorno = _compose()["services"]["app"]["environment"]
    assert entorno["SIDECAR_URL"] == "http://sidecar:3000"
    sidecar = _compose()["services"]["sidecar"]["environment"]
    assert sidecar["BACKEND_WEBHOOK_URL"] == "http://app:8000/webhook/whatsapp"


def test_dockerfile_del_sidecar_usuario_no_root_healthcheck_y_base_fijada() -> None:
    texto = (RAIZ / "sidecar" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER node" in texto
    assert "HEALTHCHECK" in texto
    assert texto.count("FROM node:22.19.0-slim") == 2  # multi-stage con la base fijada
    assert "npm ci --omit=dev" in texto  # instalación reproducible desde el lockfile
    assert 'VOLUME ["/data/baileys_auth"]' in texto


def test_la_sesion_y_node_modules_estan_ignorados_por_git_y_por_docker() -> None:
    gitignore = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert "baileys_auth/" in gitignore
    assert "sidecar/node_modules/" in gitignore
    assert "sidecar/" in (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "baileys_auth" in (RAIZ / "sidecar" / ".dockerignore").read_text(encoding="utf-8")


def test_ninguna_sesion_de_baileys_esta_versionada() -> None:
    archivos = subprocess.run(  # noqa: S603 - argumentos fijos
        ["git", "ls-files"],  # noqa: S607
        cwd=RAIZ,
        capture_output=True,
        check=True,
    ).stdout.decode()
    assert "baileys_auth" not in archivos
    assert "creds.json" not in archivos
    assert "node_modules" not in archivos


def test_ci_corre_las_pruebas_y_la_auditoria_del_sidecar_y_construye_ambas_imagenes() -> None:
    ci = yaml.safe_load((RAIZ / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    pasos = json.dumps(ci["jobs"]["sidecar"])
    assert "npm test" in pasos
    assert "npm audit --omit=dev" in pasos
    imagen = json.dumps(ci["jobs"]["imagen"])
    assert "agente-manicurista-sidecar:ci" in imagen
    assert imagen.count("trivy-action") == 2


def test_las_dependencias_del_sidecar_estan_fijadas_con_version_exacta() -> None:
    paquete = json.loads((RAIZ / "sidecar" / "package.json").read_text(encoding="utf-8"))
    for nombre, version in paquete["dependencies"].items():
        assert version[0].isdigit(), f"{nombre} no está fijada: {version}"
    # 6.7.24 ≥ 6.7.22: corrige el aviso de seguridad GHSA-qvv5-jq5g-4cgg
    assert paquete["dependencies"]["@whiskeysockets/baileys"] == "6.7.24"
