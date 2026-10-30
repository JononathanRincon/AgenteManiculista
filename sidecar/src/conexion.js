import qrcodeTerminal from 'qrcode-terminal';
import { esperaReconexionMs } from './backoff.js';
import { Emitidos } from './emitidos.js';
import { normalizarMensaje } from './mensajes.js';
import { telefonoDeJid } from './jid.js';

export class SinConexion extends Error {}
export class JidInvalido extends Error {}

/**
 * Sesión de WhatsApp (Baileys): credenciales persistentes, reconexión con backoff, QR y las tres
 * primitivas (leer, presencia, enviar). No contiene lógica de negocio. Todo lo externo se inyecta.
 */
export class Conexion {
  constructor({
    baileys,
    authDir,
    enviarEvento,
    log,
    programar = (fn, ms) => setTimeout(fn, ms),
    azar = Math.random,
    ahora = Date.now,
    mostrarQr = (qr) => qrcodeTerminal.generate(qr, { small: true }),
    emitidos = new Emitidos(),
    loggerBaileys = undefined,
  }) {
    this.baileys = baileys;
    this.authDir = authDir;
    this.enviarEvento = enviarEvento;
    this.log = log;
    this.programar = programar;
    this.azar = azar;
    this.ahora = ahora;
    this.mostrarQr = mostrarQr;
    this.emitidos = emitidos;
    this.loggerBaileys = loggerBaileys;
    this.sock = null;
    this.conexion = 'connecting';
    this.intentos = 0;
    this.qr = null;
    this.detenido = false;
    this.cola = Promise.resolve();
  }

  async iniciar() {
    const { state, saveCreds } = await this.baileys.useMultiFileAuthState(this.authDir);
    const { version } = await this.baileys.fetchLatestBaileysVersion();
    const sock = this.baileys.makeWASocket({
      auth: state,
      version,
      browser: this.baileys.Browsers.macOS('Desktop'),
      syncFullHistory: false, // no descarga ni dispara tráfico masivo al vincular
      markOnlineOnConnect: false, // no figura "en línea" y deja las notificaciones al celular
      printQRInTerminal: false, // el QR se maneja en connection.update
      ...(this.loggerBaileys ? { logger: this.loggerBaileys } : {}), // sin trazas de protocolo
    });
    this.sock = sock;
    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', (u) => this.#alCambiarConexion(u));
    sock.ev.on('messages.upsert', (u) => this.#alLlegarMensajes(u));
  }

  detener() {
    this.detenido = true;
    this.sock?.end?.(undefined);
  }

  estado() {
    return { conexion: this.conexion, qr_disponible: this.qr !== null, intentos: this.intentos };
  }

  qrAscii() {
    if (this.qr === null) return null;
    let texto = '';
    qrcodeTerminal.generate(this.qr, { small: true }, (ascii) => {
      texto = ascii;
    });
    return texto;
  }

  #alCambiarConexion({ connection, lastDisconnect, qr }) {
    if (qr) {
      this.qr = qr;
      this.log.info({ evento: 'qr_disponible' });
      this.mostrarQr(qr);
    }
    if (connection === 'open') {
      this.conexion = 'open';
      this.intentos = 0;
      this.qr = null;
      this.log.info({ evento: 'whatsapp_conectado' });
      this.#emitir({ tipo: 'estado', conexion: 'open' });
    } else if (connection === 'connecting') {
      this.conexion = 'connecting';
    } else if (connection === 'close') {
      this.#alCerrarse(lastDisconnect);
    }
  }

  #alCerrarse(lastDisconnect) {
    const codigo = lastDisconnect?.error?.output?.statusCode;
    if (codigo === this.baileys.DisconnectReason.loggedOut) {
      this.conexion = 'loggedOut';
      this.qr = null;
      this.log.fatal({ evento: 'sesion_cerrada', accion: 'volver a vincular con el QR' });
      this.#emitir({ tipo: 'estado', conexion: 'loggedOut' });
      return; // no se reconecta: haría falta un QR nuevo
    }
    this.conexion = 'close';
    this.#emitir({ tipo: 'estado', conexion: 'close' });
    if (this.detenido) return;
    this.intentos += 1;
    const espera = esperaReconexionMs(this.intentos, this.azar);
    this.log.warn({ evento: 'reconexion_programada', intento: this.intentos, espera_ms: espera, codigo });
    this.programar(() => {
      this.iniciar().catch((error) => {
        this.log.error({ evento: 'reconexion_fallida', error: error?.name ?? 'Error' });
        this.#alCerrarse({ error });
      });
    }, espera);
  }

  #emitir(evento) {
    Promise.resolve(this.enviarEvento(evento)).catch(() => {});
  }

  #alLlegarMensajes({ messages, type }) {
    for (const m of messages ?? []) {
      const evento = normalizarMensaje(m, type, this.emitidos, this.ahora());
      if (evento === null) continue;
      this.#emitir(evento);
    }
  }

  // --- primitivas ---
  #listo(jid) {
    if (telefonoDeJid(jid) === null) throw new JidInvalido(jid);
    if (this.conexion !== 'open' || this.sock === null) throw new SinConexion(this.conexion);
    return this.sock;
  }

  async leer(jid, id) {
    await this.#listo(jid).readMessages([{ remoteJid: jid, id, fromMe: false }]);
  }

  async presencia(jid, estado) {
    await this.#listo(jid).sendPresenceUpdate(estado, jid);
  }

  /** Envío serializado (orden y sin carreras); la `clave` hace idempotentes los reintentos. */
  enviar(jid, texto, clave) {
    const tarea = this.cola.then(async () => {
      const previo = this.emitidos.idDeClave(clave);
      if (previo !== null) return previo;
      const respuesta = await this.#listo(jid).sendMessage(jid, { text: texto });
      const id = respuesta?.key?.id;
      if (!id) throw new Error('sin id de mensaje');
      this.emitidos.registrar(id);
      this.emitidos.recordarClave(clave, id);
      return id;
    });
    this.cola = tarea.catch(() => {});
    return tarea;
  }
}
