import { createServer } from 'node:http';
import { tokenValido } from './firma.js';
import { JidInvalido, SinConexion } from './conexion.js';
import { esChatIndividual } from './jid.js';

const LIMITE_CUERPO = 64 * 1024;
const PRESENCIAS = new Set(['composing', 'paused', 'available', 'unavailable']);

function responder(res, codigo, cuerpo, tipo = 'application/json') {
  const datos = typeof cuerpo === 'string' ? cuerpo : JSON.stringify(cuerpo);
  res.writeHead(codigo, { 'content-type': `${tipo}; charset=utf-8`, 'cache-control': 'no-store' });
  res.end(datos);
}

function leerJson(req) {
  return new Promise((resolver, rechazar) => {
    const trozos = [];
    let total = 0;
    req.on('data', (t) => {
      total += t.length;
      if (total > LIMITE_CUERPO) {
        rechazar(Object.assign(new Error('cuerpo demasiado grande'), { codigo: 413 }));
        req.destroy();
        return;
      }
      trozos.push(t);
    });
    req.on('end', () => {
      try {
        const datos = JSON.parse(Buffer.concat(trozos).toString('utf-8') || '{}');
        if (datos === null || typeof datos !== 'object' || Array.isArray(datos)) throw new Error();
        resolver(datos);
      } catch {
        rechazar(Object.assign(new Error('JSON inválido'), { codigo: 400 }));
      }
    });
    req.on('error', rechazar);
  });
}

function validar(ruta, d) {
  const cadena = (v, max) => typeof v === 'string' && v.length > 0 && v.length <= max;
  if (!cadena(d.jid, 128) || !esChatIndividual(d.jid)) return 'jid inválido';
  if (ruta === '/leer' && !cadena(d.id, 128)) return 'id inválido';
  if (ruta === '/presencia' && !PRESENCIAS.has(d.estado)) return 'estado inválido';
  if (ruta === '/enviar' && (!cadena(d.texto, 4096) || !cadena(d.clave, 128))) {
    return 'texto o clave inválidos';
  }
  return null;
}

/** Servidor HTTP interno (solo red privada): todas las rutas exigen `Authorization: Bearer`. */
export function crearServidor({ conexion, token, log }) {
  return createServer(async (req, res) => {
    try {
      if (!tokenValido(req.headers.authorization, token)) {
        return responder(res, 401, { error: 'no_autorizado' });
      }
      const ruta = new URL(req.url, 'http://interno').pathname;
      if (req.method === 'GET' && ruta === '/estado') return responder(res, 200, conexion.estado());
      if (req.method === 'GET' && ruta === '/qr') {
        const qr = conexion.qrAscii();
        return qr === null ? responder(res, 404, { error: 'sin_qr' }) : responder(res, 200, qr, 'text/plain');
      }
      if (req.method !== 'POST' || !['/leer', '/presencia', '/enviar'].includes(ruta)) {
        return responder(res, 404, { error: 'no_encontrado' });
      }
      const datos = await leerJson(req);
      const problema = validar(ruta, datos);
      if (problema) return responder(res, 400, { error: problema });
      if (ruta === '/leer') await conexion.leer(datos.jid, datos.id);
      if (ruta === '/presencia') await conexion.presencia(datos.jid, datos.estado);
      if (ruta === '/enviar') {
        return responder(res, 200, { id: await conexion.enviar(datos.jid, datos.texto, datos.clave) });
      }
      return responder(res, 200, { ok: true });
    } catch (error) {
      if (error.codigo) return responder(res, error.codigo, { error: error.message });
      if (error instanceof SinConexion) return responder(res, 503, { error: 'sin_conexion' });
      if (error instanceof JidInvalido) return responder(res, 400, { error: 'jid inválido' });
      log.error({ evento: 'error_servidor', error: error?.name ?? 'Error' });
      return responder(res, 502, { error: 'fallo_whatsapp' });
    }
  });
}
