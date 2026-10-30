"""El blueprint de la demo respeta las restricciones de diseño y no lleva secretos."""

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]


def _servicio() -> dict[str, object]:
    datos = yaml.safe_load((RAIZ / "render.yaml").read_text(encoding="utf-8"))
    (servicio,) = datos["services"]
    assert isinstance(servicio, dict)
    return servicio


def test_demo_una_instancia_docker_con_health_check() -> None:
    s = _servicio()
    assert s["type"] == "web"
    assert s["runtime"] == "docker"
    assert s["numInstances"] == 1
    assert s["healthCheckPath"] == "/health"
    assert s["autoDeployTrigger"] == "off"  # nada se despliega sin una persona


def test_demo_variables_del_simulador_sin_secretos() -> None:
    entorno = {v["key"]: v.get("value") for v in _servicio()["envVars"]}  # type: ignore[attr-defined]
    assert entorno["SIMULADOR"] == "true"
    assert entorno["PROVEEDOR_LLM"] == "none"
    assert entorno["WEB_CONCURRENCY"] == "1"
    assert entorno["PORT"] == "8000"  # el Dockerfile escucha en 8000
    prohibidas = {
        "GEMINI_API_KEY",
        "SIDECAR_TOKEN",
        "WEBHOOK_SECRET",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
    }
    assert not prohibidas & set(entorno)


def test_la_demo_arranca_con_la_misma_imagen() -> None:
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert "--workers" in dockerfile
    assert "USER app" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "COPY app ./app" in dockerfile
