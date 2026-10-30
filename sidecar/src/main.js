import * as baileysModulo from '@whiskeysockets/baileys';
import pino from 'pino';
import { Conexion } from './conexion.js';
import { crearEmisorEventos } from './eventos.js';
import { crearServidor } from './servidor.js';

// Registro JSON mínimo: nunca incluye textos de mensajes ni números completos.
function crearLog() {
  const escribir = (nivel) => (datos) =>
    console.log(JSON.stringify({ ts: new Date().toISOString(), nivel, ...datos }));
  return {
    info: escribir('INFO'),
    warn: escribir('WARNING'),
    error: escribir('ERROR'),
    fatal: escribir('CRITICAL'),
  };
}

function requerida(nombre) {
  const valor = process.env[nombre];
  if (!valor) {
    console.error(JSON.stringify({ nivel: 'CRITICAL', evento: 'variable_faltante', variable: nombre }));
    process.exit(1);
  }
  return valor;
}

const log = crearLog();

function secreto(nombre) {
  const valor = requerida(nombre);
  if (valor.length < 32) {
    console.error(JSON.stringify({ nivel: 'CRITICAL', evento: 'secreto_corto', variable: nombre }));
    process.exit(1);
  }
  return valor;
}

const token = secreto('SIDECAR_TOKEN');
const baileys = { ...baileysModulo, makeWASocket: baileysModulo.default };
const enviarEvento = crearEmisorEventos({
  url: requerida('BACKEND_WEBHOOK_URL'),
  secreto: secreto('WEBHOOK_SECRET'),
  log,
});
const conexion = new Conexion({
  baileys,
  authDir: process.env.AUTH_DIR ?? '/data/baileys_auth',
  enviarEvento,
  log,
  // El QR da control de la cuenta: no va a los logs salvo desarrollo (SHOW_QR_STDOUT=true)
  mostrarQr: process.env.SHOW_QR_STDOUT === 'true' ? undefined : () => {},
  // Baileys en error y con redacción: sus trazas pueden traer JIDs (teléfonos completos)
  loggerBaileys: pino({
    level: 'error',
    redact: {
      paths: ['key.remoteJid', 'key.participant', 'node', '*.remoteJid', '*.participant', 'err.stack'],
      censor: '[oculto]',
    },
  }),
});
const servidor = crearServidor({ conexion, token, log });

await conexion.iniciar();
servidor.listen(Number(process.env.PORT ?? 3000), '0.0.0.0', () => log.info({ evento: 'sidecar_listo' }));

for (const senal of ['SIGINT', 'SIGTERM']) {
  process.on(senal, () => {
    conexion.detener();
    servidor.close(() => process.exit(0));
  });
}
