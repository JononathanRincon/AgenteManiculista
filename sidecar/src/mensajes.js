import { telefonoDeJid } from './jid.js';

/** Texto de un mensaje entrante (conversación, texto extendido o pie de una imagen/video). */
export function extraerTexto(mensaje) {
  if (!mensaje) return null;
  const texto =
    mensaje.conversation ??
    mensaje.extendedTextMessage?.text ??
    mensaje.imageMessage?.caption ??
    mensaje.videoMessage?.caption ??
    null;
  return typeof texto === 'string' && texto.trim() !== '' ? texto : null;
}

/** Clasifica el contenido para que el backend sepa responder con el menú ante audios o imágenes. */
export function tipoDeMensaje(mensaje) {
  if (!mensaje) return 'otro';
  if (mensaje.conversation || mensaje.extendedTextMessage) return 'texto';
  if (mensaje.audioMessage) return 'audio';
  if (mensaje.imageMessage) return 'imagen';
  if (mensaje.videoMessage) return 'video';
  if (mensaje.documentMessage) return 'documento';
  return 'otro';
}

function segundos(valor) {
  if (typeof valor === 'number') return valor;
  if (valor && typeof valor.toNumber === 'function') return valor.toNumber(); // Long de protobuf
  return null;
}

/**
 * Convierte un mensaje de Baileys en el evento que entiende el backend, o null si debe ignorarse.
 * - `emitidos`: ids que envió este sidecar (no son intervención humana).
 * - Los mensajes propios con más de `ventanaPropiosSeg` de antigüedad (sincronización) se ignoran.
 */
export function normalizarMensaje(m, tipoUpsert, emitidos, ahoraMs, ventanaPropiosSeg = 120) {
  const jid = m?.key?.remoteJid;
  const id = m?.key?.id;
  if (!id || telefonoDeJid(jid) === null) return null;
  const ts = segundos(m.messageTimestamp);
  if (m.key.fromMe) {
    if (emitidos.fueEmitido(id)) return null;
    if (ts !== null && ahoraMs / 1000 - ts > ventanaPropiosSeg) return null;
    return { tipo: 'propio', id, jid };
  }
  if (tipoUpsert !== 'notify') return null; // historial o mensajes recuperados: no se responden
  const evento = { tipo: 'mensaje', id, jid, tipo_mensaje: tipoDeMensaje(m.message) };
  const texto = extraerTexto(m.message);
  if (texto !== null && evento.tipo_mensaje === 'texto') evento.texto = texto;
  if (typeof m.pushName === 'string' && m.pushName !== '') evento.nombre = m.pushName.slice(0, 128);
  if (ts !== null) evento.ts = ts;
  return evento;
}
