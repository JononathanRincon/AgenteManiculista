# AgenteManiculista — comandos de desarrollo
# UV: en CI basta "uv"; en equipos donde uv solo está como módulo de Python: make UV="python -m uv" install
UV ?= uv
PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python,.venv/bin/python)

.PHONY: install run test lint ci

install:  ## Crea el venv con Python 3.12 y sincroniza dependencias (lockfile)
	$(UV) sync --python 3.12

run:  ## Arranca la API (un solo proceso: el worker único y SQLite lo exigen)
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

test:  ## Pruebas con cobertura de ramas (umbral en pyproject.toml)
	$(PY) -m pytest --cov --cov-branch --cov-report=term-missing

lint:  ## Lint, formato y tipos estrictos
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy

ci: lint test  ## Lo mismo que corre el pipeline
	$(UV) export --no-hashes --no-emit-project -o requirements-ci.txt
	$(PY) -m pip_audit -r requirements-ci.txt --disable-pip --no-deps
