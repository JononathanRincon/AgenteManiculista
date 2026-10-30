import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { test } from 'node:test';
import { Conexion, JidInvalido, SinConexion } from '../src/conexion.js';

const JID = '573001112233@s.whatsapp.net';
const log = { info() {}, warn() {}, error() {}, fatal() {} };

function montar(opciones = {}) {
  const socks = [];
  const llamadas = { readMessages: [], sendPresenceUpdate: [], sendMessage: [], configs: [], creds: 0, end: 0 };
  const nuevoSock = () => ({
    ev: new EventEmitter(),
    readMessages: async (k) => llamadas.readMessages.push(k),
    sendPresenceUpdate: async (e, j) => llamadas.sendPresenceUpdate.push([e, j]),
    sendMessage: async (j, c) => {
      llamadas.sendMessage.push([j, c]);
      return { key: { id: `ID-${llamadas.sendMessage.length}` } };
    },
    end: () => (llamadas.end += 1),
  });
  const baileys = {
    makeWASocket: (config) => {
      llamadas.configs.push(config);
      socks.push(nuevoSock());
      return socks.at(-1);
    },
    useMultiFileAuthState: async (dir) => ({ state: { dir }, saveCreds: async () => (llamadas.creds += 1) }),
    fetchLatestBaileysVersion: async () => ({ version: [2, 3000, 1] }),
    Browsers: { macOS: (n) => ['Mac OS', n, '14.4.1'] },
    DisconnectReason: { loggedOut: 401 },
  };
  const eventos = [];
  const programados = [];
  const qrs = [];
  const conexion = new Conexion({
    baileys,
    authDir: '/data/baileys_auth',
    enviarEvento: async (e) => eventos.push(e),
    log,
    programar: (fn, ms) => programados.push({ fn, ms }),
    azar: () => 0,
    ahora: () => 1_800_000_000_000,
    mostrarQr: (qr) => qrs.push(qr),
    ...opciones,
  });
  return {
    conexion,
    get ev() {
      return socks.at(-1).ev; // cada reconexión crea un socket nuevo con su propio emisor
    },
    get sock() {
      return socks.at(-1);
    },
    llamadas,
    eventos,
    programados,
    qrs,
  };
}

const abrir = (t) => t.ev.emit('connection.update', { connection: 'open' });

test('arranque: credenciales persistentes, versión vigente y huella coherente', async () => {
  const t = montar();
  await t.conexion.iniciar();
  const c = t.llamadas.configs[0];
  assert.deepEqual(c.auth, { dir: '/data/baileys_auth' });
  assert.deepEqual(c.version, [2, 3000, 1]);
  assert.deepEqual(c.browser, ['Mac OS', 'Desktop', '14.4.1']);
  assert.equal(c.syncFullHistory, false);
  assert.equal(c.markOnlineOnConnect, false);
  assert.equal(c.printQRInTerminal, false);
  t.ev.emit('creds.update', {});
  await new Promise((r) => setImmediate(r));
  assert.equal(t.llamadas.creds, 1); // las credenciales se guardan en disco
});

test('QR: se muestra, queda disponible y se borra al conectar', async () => {
  const t = montar();
  await t.conexion.iniciar();
  t.ev.emit('connection.update', { qr: 'CODIGO-QR' });
  assert.deepEqual(t.qrs, ['CODIGO-QR']);
  assert.equal(t.conexion.estado().qr_disponible, true);
  assert.equal(typeof t.conexion.qrAscii(), 'string');
  assert.ok(t.conexion.qrAscii().length > 20);
  abrir(t);
  assert.equal(t.conexion.estado().qr_disponible, false);
  assert.equal(t.conexion.qrAscii(), null);
});

test('conectado: avisa al backend y reinicia los intentos', async () => {
  const t = montar();
  await t.conexion.iniciar();
  t.ev.emit('connection.update', { connection: 'connecting' });
  assert.equal(t.conexion.estado().conexion, 'connecting');
  abrir(t);
  assert.deepEqual(t.eventos.at(-1), { tipo: 'estado', conexion: 'open' });
  assert.equal(t.conexion.estado().intentos, 0);
});

test('desconexión distinta de loggedOut: reconecta con backoff 2, 4, 8 s', async () => {
  const t = montar();
  await t.conexion.iniciar();
  const cierre = { connection: 'close', lastDisconnect: { error: { output: { statusCode: 428 } } } };
  t.ev.emit('connection.update', cierre);
  t.ev.emit('connection.update', cierre);
  t.ev.emit('connection.update', cierre);
  assert.deepEqual(t.programados.map((p) => p.ms), [2000, 4000, 8000]);
  assert.equal(t.conexion.estado().conexion, 'close');
  assert.deepEqual(t.eventos.at(-1), { tipo: 'estado', conexion: 'close' });
  await t.programados[0].fn(); // al vencer el temporizador se vuelve a iniciar
  await new Promise((r) => setImmediate(r));
  assert.equal(t.llamadas.configs.length, 2);
  abrir(t);
  t.ev.emit('connection.update', cierre);
  assert.equal(t.programados.at(-1).ms, 2000); // tras reconectar el backoff vuelve a empezar
});

test('loggedOut (401): alerta crítica, aviso al backend y NO reconecta', async () => {
  const fatales = [];
  const t = montar({ log: { ...log, fatal: (d) => fatales.push(d) } });
  await t.conexion.iniciar();
  t.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 401 } } } });
  assert.equal(t.conexion.estado().conexion, 'loggedOut');
  assert.deepEqual(t.eventos.at(-1), { tipo: 'estado', conexion: 'loggedOut' });
  assert.equal(t.programados.length, 0);
  assert.equal(fatales.length, 1);
  assert.equal(fatales[0].evento, 'sesion_cerrada');
});

test('si la reconexión falla se vuelve a programar con más espera', async () => {
  const t = montar();
  await t.conexion.iniciar();
  t.conexion.baileys.fetchLatestBaileysVersion = async () => {
    throw new Error('sin red');
  };
  t.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 500 } } } });
  await t.programados[0].fn();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(t.programados.map((p) => p.ms), [2000, 4000]);
});

test('detener() no reconecta', async () => {
  const t = montar();
  await t.conexion.iniciar();
  t.conexion.detener();
  assert.equal(t.llamadas.end, 1);
  t.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 500 } } } });
  assert.equal(t.programados.length, 0);
});

const upsert = (mensajes, type = 'notify') => ({ messages: mensajes, type });
const mensaje = (extra = {}, key = {}) => ({
  key: { remoteJid: JID, id: 'M1', fromMe: false, ...key },
  message: { conversation: 'hola' },
  pushName: 'Ana',
  messageTimestamp: 1_800_000_000,
  ...extra,
});

test('mensajes entrantes: solo chats individuales llegan al backend', async () => {
  const t = montar();
  await t.conexion.iniciar();
  t.ev.emit('messages.upsert', upsert([
    mensaje(),
    mensaje({}, { id: 'G1', remoteJid: '120363@g.us' }),
    mensaje({}, { id: 'S1', remoteJid: 'status@broadcast' }),
    mensaje({}, { id: 'C1', remoteJid: '1203@newsletter' }),
  ]));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(t.eventos.map((e) => e.id), ['M1']);
  assert.equal(t.eventos[0].tipo, 'mensaje');
  t.ev.emit('messages.upsert', { messages: undefined, type: 'notify' }); // evento vacío: no falla
});

test('intervención humana: lo que la dueña escribe a mano se informa; lo que envió el bot, no', async () => {
  const t = montar();
  await t.conexion.iniciar();
  abrir(t);
  const id = await t.conexion.enviar(JID, 'Hola', 'clave-1');
  t.ev.emit('messages.upsert', upsert([
    mensaje({}, { fromMe: true, id }), // lo envió el propio sidecar
    mensaje({}, { fromMe: true, id: 'DUENA-1' }), // lo escribió la dueña desde su celular
  ]));
  await new Promise((r) => setImmediate(r));
  const propios = t.eventos.filter((e) => e.tipo === 'propio');
  assert.deepEqual(propios.map((e) => e.id), ['DUENA-1']);
});

test('primitivas: leer, presencia y enviar con las llamadas exactas de Baileys', async () => {
  const t = montar();
  await t.conexion.iniciar();
  abrir(t);
  await t.conexion.leer(JID, 'IN-1');
  await t.conexion.presencia(JID, 'composing');
  await t.conexion.presencia(JID, 'paused');
  assert.deepEqual(t.llamadas.readMessages, [[{ remoteJid: JID, id: 'IN-1', fromMe: false }]]);
  assert.deepEqual(t.llamadas.sendPresenceUpdate, [['composing', JID], ['paused', JID]]);
  assert.equal(await t.conexion.enviar(JID, 'Hola', 'k1'), 'ID-1');
  assert.deepEqual(t.llamadas.sendMessage, [[JID, { text: 'Hola' }]]);
});

test('el reintento con la misma clave no duplica el envío', async () => {
  const t = montar();
  await t.conexion.iniciar();
  abrir(t);
  const [a, b] = await Promise.all([t.conexion.enviar(JID, 'Hola', 'k'), t.conexion.enviar(JID, 'Hola', 'k')]);
  assert.equal(a, b);
  assert.equal(t.llamadas.sendMessage.length, 1);
});

test('los envíos se serializan en orden', async () => {
  const t = montar();
  await t.conexion.iniciar();
  abrir(t);
  await Promise.all(['a', 'b', 'c'].map((x) => t.conexion.enviar(JID, x, `k-${x}`)));
  assert.deepEqual(t.llamadas.sendMessage.map(([, c]) => c.text), ['a', 'b', 'c']);
});

test('sin conexión abierta o con jid inválido se rechaza', async () => {
  const t = montar();
  await t.conexion.iniciar();
  await assert.rejects(() => t.conexion.leer(JID, 'x'), SinConexion);
  await assert.rejects(() => t.conexion.enviar(JID, 'x', 'k'), SinConexion);
  abrir(t);
  await assert.rejects(() => t.conexion.presencia('120363@g.us', 'composing'), JidInvalido);
  assert.equal(t.llamadas.sendMessage.length, 0);
});

test('un envío sin id de mensaje es un error y no se recuerda', async () => {
  const t = montar();
  await t.conexion.iniciar();
  abrir(t);
  t.sock.sendMessage = async () => undefined;
  await assert.rejects(() => t.conexion.enviar(JID, 'x', 'k'), /sin id/);
  assert.equal(t.conexion.emitidos.idDeClave('k'), null);
  t.sock.sendMessage = async () => ({ key: { id: 'OK-1' } }); // la cola sigue funcionando
  assert.equal(await t.conexion.enviar(JID, 'x', 'k2'), 'OK-1');
});
