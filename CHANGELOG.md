# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). El proyecto aún no
tiene release publicado (el tag `vX.Y.Z` requiere confirmación explícita).

## [Sin publicar]

### Agregado
- Agendamiento por WhatsApp con máquina de estados, disponibilidad determinista y anti doble reserva
  (reserva + bloques + trabajo en una transacción `BEGIN IMMEDIATE`, worker único con verificación en vivo).
- Cola FIFO con reintentos, backoff y pausa por cuota; reservas PENDIENTE/CONFIRMADA/CANCELADA/EXPIRADA/FALLIDA.
- Capa de intención con Gemini opcional (`PROVEEDOR_LLM=none` funciona completo), cuotas y cortacircuitos;
  el LLM nunca recibe datos personales.
- Transporte WhatsApp vía sidecar Baileys (ADR-009) con controles anti-ban: humanización, menús
  numerados, debounce, pausa por intervención humana, límites de tasa, webhook firmado con HMAC y marca de tiempo.
- Adaptador de Google Calendar con cuenta de servicio, espejo con `syncToken`, canal push
  (`events.watch`) con renovación y modo degradado.
- Cableado de producción en `crear_app` (`ENTORNO=produccion`).
- Simulador `/demo` con calendario simulado y blueprint de Render (no desplegado).
- Documentación: SDD v0.4, README, manual de la dueña, runbook, modelo de amenazas, ADR.

### Seguridad
- Revisiones por bloque (0 críticos, 0 altos). Secretos de ≥ 32 caracteres, contenedores sin root,
  `SIMULADOR` prohibido en producción, `GEMINI_TIER=paid` exigido en producción con Gemini.

### Limitaciones conocidas
- Baileys es no oficial y puede provocar restricciones del número de WhatsApp.
- Gemini, el envío real por WhatsApp y Google Calendar real no se han probado con cuentas reales.
- Recordatorios de cita: fuera de alcance (extensión futura).
