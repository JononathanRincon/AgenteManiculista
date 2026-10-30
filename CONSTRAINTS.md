# CONSTRAINTS.md — Vara de calidad del proyecto
## AgenteManiculista

> Creado una sola vez (Fase 0.0) partiendo de `DevSenPy` y ajustado con el stack del SDD v0.3 (Fase 1.5).
> **Ningún paso del loop puede rebajar estos umbrales.** Para cambiarlos hace falta orden explícita del
> usuario y una entrada en `AI_PROCESS.md`.

## Calidad de código
| Restricción | Umbral | Herramienta |
|---|---|---|
| Tipado | `mypy --strict` sin errores | mypy |
| Lint y formato | 0 errores | ruff (`check` y `format --check`) |
| Cobertura de ramas | ≥ 85 % general; **100 %** en `disponibilidad`, `reservas`, `cola`/`worker`, verificación de firma y máquina de estados | `coverage run --branch` / `pytest --cov --cov-branch` |
| Tests | 100 % en verde por tarea | pytest |
| Complejidad | Funciones ≤ 50 líneas y complejidad ciclomática ≤ 10 (regla ruff `C901`) | ruff |

## Prohibido bajar la vara
- `# type: ignore`, `# noqa`, `# pragma: no cover` sin motivo escrito en el mismo comentario.
- Tests borrados, `skip`/`xfail` o aserciones debilitadas para hacer pasar una tarea.
- Reducir umbrales de cobertura o desactivar reglas de lint/mypy.
- `except Exception:` desnudo, `verify=False`, secretos en código o repositorio.
- Strings visibles al usuario fuera de `config/mensajes.yaml`.

## Rendimiento y operación
| Restricción | Umbral |
|---|---|
| Respuesta del webhook | < 1 s p95; nunca > 5 s |
| Timeout LLM | 3 s |
| Timeouts HTTP | Graph 10 s, Calendar 10 s |
| Procesos | `uvicorn --workers 1`, una instancia |
| Imagen | multi-stage, no root, `HEALTHCHECK` |

## Seguridad
- Sin `.env*` (salvo `.env.example` sin valores), `*.pem`, `*.key` en el repositorio.
- Teléfono siempre enmascarado en logs; nunca tokens ni contenido enviado al LLM.
- Auditoría de dependencias y escaneo de secretos en CI sin hallazgos altos o críticos.
- Dependencia nueva solo con verificación en PyPI y justificación en `AI_PROCESS.md`.

## Cumplimiento
- Ley 1581 de 2012: aviso y autorización antes de tratar datos; retención según SDD §6.5.
