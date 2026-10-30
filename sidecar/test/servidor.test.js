import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { JidInvalido, SinConexion } from '../src/conexion.js';
import { crearServidor } from '../src/servidor.js';

const TOKEN = 'token-de-prueba';
const JID = '573001112233@s.whatsapp.net';
const log = { info() {}, warn() {}, error() {}, fatal() {} };

const llamadas = [];
let modoFallo = null;
const conexionFalsa = {
  estado: () => ({ conexion: 'open', qr_disponible: false, intentos: 0 }),
  qrAscii: () => (modoFallo === 'qr' ? 'ASCII-QR' : null),
  async leer(jid, id) {
    llamadas.push(['leer', jid, id]);
    this.fallar();
  },
  async presencia(jid, estado) {
    llamadas.push(['presencia', jid, estado]);
    this.fallar();
  },
  async enviar(jid, texto, clave) {
    llamadas.push(['enviar', jid, texto, clave]);
    this.fallar();
    return 'ID-7';
  },
  fallar() {
    if (modoFallo === 'sin_conexion') throw new SinConexion('close');
    if (modoFallo === 'jid') throw new JidInvalido('x');
    if (modoFallo === 'whatsapp') throw new Error('boom');
  },
};

let servidor;
let base;
before(async () => {
  servidor = crearServidor({ conexion: conexionFalsa, token: TOKEN, log });
  await new Promise((r) => servidor.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${servidor.address().port}`;
});
after(() => servidor.close());

const pedir = (ruta, { metodo = 'POST', cuerpo, token = TOKEN, crudo } = {}) =>
  fetch(base + ruta, {
    method: metodo,
    headers: { ...(token ? { authorization: `Bearer ${token}` } : {}), 'content-type': 'application/json' },
    body: metodo === 'GET' ? undefined : (crudo ?? (cuerpo === undefined ? undefined : JSON.stringify(cuerpo))),
  });

test('toda ruta exige el token del backend', async () => {
  for (const [ruta, metodo] of [['/estado', 'GET'], ['/qr', 'GET'], ['/leer', 'POST'], ['/enviar', 'POST']]) {
    assert.equal((await pedir(ruta, { metodo, token: null, cuerpo: {} })).status, 401);
    assert.equal((await pedir(ruta, { metodo, token: 'otro', cuerpo: {} })).status, 401);
  }
  assert.equal(llamadas.length, 0);
});

test('estado y QR', async () => {
  const estado = await pedir('/estado', { metodo: 'GET' });
  assert.deepEqual(await estado.json(), { conexion: 'open', qr_disponible: false, intentos: 0 });
  assert.equal((await pedir('/qr', { metodo: 'GET' })).status, 404);
  modoFallo = 'qr';
  const qr = await pedir('/qr', { metodo: 'GET' });
  assert.equal(qr.status, 200);
  assert.match(qr.headers.get('content-type'), /text\/plain/);
  assert.equal(await qr.text(), 'ASCII-QR');
  modoFallo = null;
});

test('leer, presencia y enviar', async () => {
  assert.equal((await pedir('/leer', { cuerpo: { jid: JID, id: 'IN-1' } })).status, 200);
  assert.equal((await pedir('/presencia', { cuerpo: { jid: JID, estado: 'composing' } })).status, 200);
  const r = await pedir('/enviar', { cuerpo: { jid: JID, texto: 'Hola', clave: 'k1' } });
  assert.deepEqual(await r.json(), { id: 'ID-7' });
  assert.deepEqual(llamadas.slice(-3).map((l) => l[0]), ['leer', 'presencia', 'enviar']);
});

test('validaciones: jid de grupo, estado, texto y JSON roto', async () => {
  const casos = [
    ['/leer', { jid: '120363@g.us', id: 'x' }],
    ['/leer', { jid: JID }],
    ['/presencia', { jid: JID, estado: 'recording' }],
    ['/enviar', { jid: JID, texto: '', clave: 'k' }],
    ['/enviar', { jid: JID, texto: 'hola' }],
    ['/enviar', { jid: JID, texto: 'x'.repeat(4097), clave: 'k' }],
    ['/enviar', { jid: 5, texto: 'hola', clave: 'k' }],
  ];
  for (const [ruta, cuerpo] of casos) assert.equal((await pedir(ruta, { cuerpo })).status, 400, JSON.stringify(cuerpo).slice(0, 60));
  assert.equal((await pedir('/enviar', { crudo: '{no es json' })).status, 400);
  assert.equal((await pedir('/enviar', { crudo: '[1,2]' })).status, 400);
});

test('cuerpo demasiado grande', async () => {
  const r = await pedir('/enviar', { crudo: JSON.stringify({ jid: JID, texto: 'x'.repeat(70_000), clave: 'k' }) }).catch(() => null);
  assert.ok(r === null || r.status === 413); // el servidor corta la conexión o responde 413
});

test('rutas desconocidas y métodos no permitidos', async () => {
  assert.equal((await pedir('/nada', { metodo: 'GET' })).status, 404);
  assert.equal((await pedir('/leer', { metodo: 'GET' })).status, 404);
  assert.equal((await pedir('/estado', { metodo: 'POST', cuerpo: {} })).status, 404);
});

test('errores de WhatsApp: sin conexión 503, jid 400, otros 502', async () => {
  const cuerpo = { jid: JID, texto: 'hola', clave: 'k2' };
  modoFallo = 'sin_conexion';
  assert.equal((await pedir('/enviar', { cuerpo })).status, 503);
  modoFallo = 'jid';
  assert.equal((await pedir('/enviar', { cuerpo })).status, 400);
  modoFallo = 'whatsapp';
  const r = await pedir('/enviar', { cuerpo });
  assert.equal(r.status, 502);
  assert.deepEqual(await r.json(), { error: 'fallo_whatsapp' }); // sin filtrar el mensaje interno
  modoFallo = null;
});
