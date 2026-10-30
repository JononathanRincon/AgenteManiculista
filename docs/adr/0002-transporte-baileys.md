# ADR-009 — Transporte de WhatsApp vía Baileys con controles anti-ban

- **Estado:** autorizada por el usuario (chat, 2026-10-30); texto del SDD v0.4 pendiente de lectura.
- **Contexto:** la dueña no puede migrar su número a la WhatsApp Cloud API porque perdería el uso de la
  aplicación de WhatsApp Business en su celular. Hay que proteger su línea contra bloqueos por
  detección de automatización.
- **Decisión:** reemplazar el adaptador de Graph API por un **sidecar Node.js propio** con
  `@whiskeysockets/baileys` (dispositivo vinculado por QR) conectado a FastAPI por el puerto `Mensajeria`
  y por `/webhook/whatsapp` (HMAC). Toda la política anti-bloqueo vive en Python (testeable con reloj
  inyectable): humanización de envíos, menús numerados, debounce, handoff humano y rate limit. El
  dominio no cambia.
- **Alternativas consideradas:**
  1. *Evolution API (basada en Baileys).* Añade un producto completo, base de datos propia y mayor
     superficie de ataque; se descarta para el MVP, pero queda válida detrás del mismo puerto.
  2. *Cloud API con número nuevo.* Oficial y sin riesgo de ToS, pero la dueña perdería la app en ese
     número; queda como **plan B**.
  3. *Lógica anti-ban dentro del sidecar.* Más difícil de probar y de cubrir al 100 %; se descarta.
- **Consecuencias:**
  - (+) La dueña conserva su celular y puede intervenir a mano; el bot se pausa solo.
  - (−) **Riesgo residual de bloqueo del número:** Baileys no es oficial y su uso automatizado puede
    contravenir los Términos de Servicio de WhatsApp; los controles lo reducen, no lo eliminan. La dueña
    lo acepta; se documenta en el README.
  - (−) Un segundo runtime (Node.js 22) y un segundo contenedor.
  - (−) Los menús son texto numerado, no botones nativos.
  - Versión de Baileys fijada en ≥ 6.7.22 (o 7.0.0-rc12) por el aviso de seguridad GHSA-qvv5-jq5g-4cgg; la
    línea 7.x está en candidata a versión y la 6.7.x es la etiquetada `legacy`: se decide en TASK-70
    verificando el registro de npm.
  - Las tareas de Cloud API (TASK-41, 42, 43) se reemplazan por TASK-63 a TASK-72.
