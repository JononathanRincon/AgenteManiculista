# Informe go/no-go (TASK-51)

Fecha: 2026-10-30 · Rama evaluada: `develop` · **Decisión del agente: NO-GO a producción por ahora;
GO condicionado para la demo del simulador.** El go explícito lo da el usuario, no este informe.

## Verificaciones ejecutadas (evidencia)

| Verificación | Resultado |
|---|---|
| `ruff check` y `ruff format --check` | Limpio (119 archivos) |
| `mypy --strict` | Limpio (101 archivos) |
| Pruebas Python | 808 pasan, 0 fallan |
| Cobertura de ramas (medida) | 99 % total; 100 % en disponibilidad, reservas, cola/worker, firma, máquina de estados, webhook, calendario y canal |
| Pruebas del sidecar (Node 22) | 40 pasan, 0 fallan |
| `pip-audit` (dependencias Python) | Sin vulnerabilidades conocidas |
| `npm audit --omit=dev` (sidecar) | 0 vulnerabilidades |
| `docker compose --profile whatsapp build` | Ambas imágenes construyen |
| Revisiones de seguridad por bloque | 0 críticos, 0 altos abiertos (Bloques 8 y 9) |

## Condiciones diferidas del SDD §17

| ID | Condición | Estado |
|---|---|---|
| PA-07 | Revisión legal del aviso de tratamiento de datos (Ley 1581) | **Abierta**: el texto de `config/mensajes.yaml` es un borrador sin revisión legal |
| PA-09 | Repositorio y proveedor de producción | **Abierta**: no hay remoto, nada se ha subido; proveedor sin decidir |
| PA-10 | Métricas base del negocio | **Abierta**: sin levantar con la manicurista |
| PA-15 | Monto real del recargo de domicilio | **Abierta**: `config/negocio.yaml` trae `recargo_cop: 0` y domicilio deshabilitado; precios de `servicios.yaml` son de ejemplo |

## Lo que NUNCA se ha probado con cuentas reales

- Envío y recepción reales por WhatsApp (Baileys): se probó el sidecar hasta generar el QR, sin vincular un número.
- Google Calendar real (cuenta de servicio, `events.watch` con una URL pública real).
- Gemini real (llamada en vivo con clave).
- Cómo expande Google las recurrencias sin `timeMax` en el listado completo.

## Riesgos aceptados que el responsable debe conocer

1. **Baileys es no oficial y va contra los términos de servicio de WhatsApp**: el número puede ser restringido. Plan B: WhatsApp Cloud API (obliga a migrar el número fuera de WhatsApp Business).
2. El texto de SDD v0.4 lo redactó el agente a partir de la autorización del usuario; **falta su lectura y confirmación**.
3. Acciones de CI e imágenes sin fijar por SHA/digest (se hace al preparar el release).
4. Sin proxy inverso definido: `/webhook/whatsapp` y `/admin` no deben exponerse (PA-09).
5. Cifrado de respaldos y rotación de secretos descritos en el runbook, no automatizados.

## Para dar el GO a producción faltan

1. Cerrar PA-07, PA-09, PA-10 y PA-15.
2. Prueba de humo con cuentas reales: vincular un número de prueba, compartir un calendario de prueba, una cita completa de extremo a extremo y cancelación.
3. Decidir proveedor con disco persistente y HTTPS público.
4. Confirmar la lectura de SDD v0.4.

## Acciones que requieren confirmación explícita del usuario (no ejecutadas)

- **TASK-61:** desplegar la demo en Render (plan y costo a confirmar).
- **TASK-62:** `release/vX.Y.Z`, merge a `main` y tag.
- Cualquier `git push`.
