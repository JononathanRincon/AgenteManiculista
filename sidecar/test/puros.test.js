import assert from 'node:assert/strict';
import { test } from 'node:test';
import { esperaReconexionMs } from '../src/backoff.js';
import { Emitidos } from '../src/emitidos.js';
import { crearEmisorEventos } from '../src/eventos.js';
import { firmar, iguales, tokenValido } from '../src/firma.js';
import { esChatIndividual, telefonoDeJid } from '../src/jid.js';
import { extraerTexto, normalizarMensaje, tipoDeMensaje } from '../src/mensajes.js';

const SIN_AZAR = () => 0;

test('backoff de reconexión: 2, 4, 8, 16, 32 y tope de 60 s, más jitter', () => {
  const esperas = [1, 2, 3, 4, 5, 6, 7, 20].map((n) => esperaReconexionMs(n, SIN_AZAR));
  assert.deepEqual(esperas, [2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000]);
  assert.equal(esperaReconexionMs(1, () => 1), 3000);
  assert.equal(esperaReconexionMs(0, SIN_AZAR), 2000); // nunca menos que el primer escalón
  for (let i = 0; i < 100; i += 1) {
    const ms = esperaReconexionMs(3);
    assert.ok(ms >= 8000 && ms <= 9000);
  }
});

test('solo los chats individuales tienen teléfono', () => {
  assert.equal(telefonoDeJid('573001112233@s.whatsapp.net'), '573001112233');
  assert.equal(telefonoDeJid('573001112233:12@s.whatsapp.net'), '573001112233');
  for (const jid of ['120363@g.us', 'status@broadcast', '1203@newsletter', '99999999999@lid', 'abc@s.whatsapp.net', '123@s.whatsapp.net', '', null, undefined, 42]) {
    assert.equal(telefonoDeJid(jid), null, String(jid));
    assert.equal(esChatIndividual(jid), false);
  }
  assert.equal(esChatIndividual('573001112233@s.whatsapp.net'), true);
});

test('firma HMAC compatible con el backend de Python', () => {
  // vector calculado con hmac.new(b'secreto-de-prueba', b'{"a":1}', sha256) en Python
  assert.equal(
    firmar('secreto-de-prueba', '{"a":1}'),
    'sha256=75c29ecad8889dd6f2964c96b138dbf7567ebee0d21ad8305bbc4affdf67034e',
  );
});

test('comparaciones en tiempo constante', () => {
  assert.equal(iguales('abc', 'abc'), true);
  assert.equal(iguales('abc', 'abd'), false);
  assert.equal(iguales('abc', 'abcd'), false);
  assert.equal(tokenValido('Bearer secreto', 'secreto'), true);
  assert.equal(tokenValido('Bearer otro', 'secreto'), false);
  assert.equal(tokenValido('secreto', 'secreto'), false);
  assert.equal(tokenValido(undefined, 'secreto'), false);
  assert.equal(tokenValido('Bearer ', ''), false); // sin token configurado nada entra
});

test('extracción de texto y tipo de mensaje', () => {
  assert.equal(extraerTexto({ conversation: 'hola' }), 'hola');
  assert.equal(extraerTexto({ extendedTextMessage: { text: 'respuesta' } }), 'respuesta');
  assert.equal(extraerTexto({ imageMessage: { caption: 'mira' } }), 'mira');
  assert.equal(extraerTexto({ conversation: '   ' }), null);
  assert.equal(extraerTexto({ audioMessage: {} }), null);
  assert.equal(extraerTexto(null), null);
  assert.equal(tipoDeMensaje({ conversation: 'x' }), 'texto');
  assert.equal(tipoDeMensaje({ extendedTextMessage: {} }), 'texto');
  assert.equal(tipoDeMensaje({ audioMessage: {} }), 'audio');
  assert.equal(tipoDeMensaje({ imageMessage: {} }), 'imagen');
  assert.equal(tipoDeMensaje({ videoMessage: {} }), 'video');
  assert.equal(tipoDeMensaje({ documentMessage: {} }), 'documento');
  assert.equal(tipoDeMensaje({ stickerMessage: {} }), 'otro');
  assert.equal(tipoDeMensaje(undefined), 'otro');
});

const AHORA_MS = 1_800_000_000_000;
const ahoraS = AHORA_MS / 1000;
const msg = (extra = {}, key = {}) => ({
  key: { remoteJid: '573001112233@s.whatsapp.net', id: 'A1', fromMe: false, ...key },
  message: { conversation: 'hola' },
  pushName: 'María',
  messageTimestamp: ahoraS,
  ...extra,
});

test('mensaje entrante individual de texto', () => {
  const evento = normalizarMensaje(msg(), 'notify', new Emitidos(), AHORA_MS);
  assert.deepEqual(evento, {
    tipo: 'mensaje',
    id: 'A1',
    jid: '573001112233@s.whatsapp.net',
    tipo_mensaje: 'texto',
    texto: 'hola',
    nombre: 'María',
    ts: ahoraS,
  });
});

test('un audio no lleva texto y el timestamp puede venir como Long', () => {
  const evento = normalizarMensaje(
    msg({ message: { audioMessage: {} }, messageTimestamp: { toNumber: () => ahoraS }, pushName: '' }),
    'notify',
    new Emitidos(),
    AHORA_MS,
  );
  assert.equal(evento.tipo_mensaje, 'audio');
  assert.equal('texto' in evento, false);
  assert.equal('nombre' in evento, false);
  assert.equal(evento.ts, ahoraS);
});

test('grupos, difusión, canales y mensajes sin id se ignoran', () => {
  const e = new Emitidos();
  for (const remoteJid of ['120363@g.us', 'status@broadcast', '1203@newsletter']) {
    assert.equal(normalizarMensaje(msg({}, { remoteJid }), 'notify', e, AHORA_MS), null);
  }
  assert.equal(normalizarMensaje(msg({}, { id: undefined }), 'notify', e, AHORA_MS), null);
  assert.equal(normalizarMensaje({}, 'notify', e, AHORA_MS), null);
});

test('el historial (append) no se responde', () => {
  assert.equal(normalizarMensaje(msg(), 'append', new Emitidos(), AHORA_MS), null);
});

test('mensaje propio: intervención humana solo si no lo envió el bot y es reciente', () => {
  const e = new Emitidos();
  const propio = msg({}, { fromMe: true, id: 'DUENA-1' });
  assert.deepEqual(normalizarMensaje(propio, 'notify', e, AHORA_MS), {
    tipo: 'propio',
    id: 'DUENA-1',
    jid: '573001112233@s.whatsapp.net',
  });
  e.registrar('MIO-1');
  assert.equal(normalizarMensaje(msg({}, { fromMe: true, id: 'MIO-1' }), 'notify', e, AHORA_MS), null);
  const viejo = msg({ messageTimestamp: ahoraS - 3600 }, { fromMe: true, id: 'VIEJO' });
  assert.equal(normalizarMensaje(viejo, 'append', e, AHORA_MS), null); // sincronización de historial
  const sinMarca = msg({ messageTimestamp: undefined }, { fromMe: true, id: 'SIN-TS' });
  assert.equal(normalizarMensaje(sinMarca, 'append', e, AHORA_MS).tipo, 'propio');
});

test('memoria de emitidos acotada e idempotencia por clave', () => {
  const e = new Emitidos(3);
  for (const id of ['a', 'b', 'c', 'd']) e.registrar(id);
  assert.equal(e.fueEmitido('a'), false);
  assert.equal(e.fueEmitido('d'), true);
  for (const [clave, id] of [['k1', '1'], ['k2', '2'], ['k3', '3'], ['k4', '4']]) e.recordarClave(clave, id);
  assert.equal(e.idDeClave('k1'), null);
  assert.equal(e.idDeClave('k4'), '4');
});

const logSilencioso = { info() {}, warn() {}, error() {}, fatal() {} };

test('entrega de eventos: firma, reintentos y rendición ante 4xx', async () => {
  const llamadas = [];
  const esperas = [];
  const respuestas = [{ ok: false, status: 503 }, new Error('red'), { ok: true, status: 200 }];
  const fetchFn = async (url, opciones) => {
    llamadas.push({ url, ...opciones });
    const r = respuestas.shift();
    if (r instanceof Error) throw r;
    return r;
  };
  const enviar = crearEmisorEventos({ url: 'http://backend/webhook', secreto: 's', fetchFn, dormir: async (ms) => esperas.push(ms), log: logSilencioso });
  assert.equal(await enviar({ tipo: 'estado', conexion: 'open' }), true);
  assert.equal(llamadas.length, 3);
  assert.deepEqual(esperas, [1000, 2000]);
  assert.equal(llamadas[0].headers['x-firma'], firmar('s', llamadas[0].body));
  // un 403 no se reintenta
  llamadas.length = 0;
  respuestas.push({ ok: false, status: 403 });
  assert.equal(await enviar({ tipo: 'x' }), false);
  assert.equal(llamadas.length, 1);
  // siempre caído: agota los intentos
  llamadas.length = 0;
  const caido = crearEmisorEventos({ url: 'u', secreto: 's', fetchFn: async () => { throw new Error('x'); }, dormir: async () => {}, log: logSilencioso });
  assert.equal(await caido({ tipo: 'x' }), false);
});
