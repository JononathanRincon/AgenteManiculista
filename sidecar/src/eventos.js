import { firmar } from './firma.js';

const ESPERAS_MS = [1000, 2000, 4000];

/**
 * Entrega eventos firmados al backend (POST /webhook/whatsapp) con reintentos.
 * Devuelve true si el backend aceptó el evento.
 */
export function crearEmisorEventos({ url, secreto, fetchFn = fetch, dormir = defaultDormir, log }) {
  return async function enviarEvento(evento) {
    const cuerpo = JSON.stringify(evento);
    for (let intento = 0; intento <= ESPERAS_MS.length; intento += 1) {
      try {
        const r = await fetchFn(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-firma': firmar(secreto, cuerpo) },
          body: cuerpo,
          signal: AbortSignal.timeout(10_000),
        });
        if (r.ok) return true;
        if (r.status < 500 && r.status !== 429) {
          log.error({ evento: 'backend_rechazo', estado: r.status });
          return false; // 403/422: reintentar no cambia nada
        }
      } catch (error) {
        log.warn({ evento: 'backend_inalcanzable', error: error?.name ?? 'Error' });
      }
      if (intento < ESPERAS_MS.length) await dormir(ESPERAS_MS[intento]);
    }
    log.error({ evento: 'backend_sin_respuesta' });
    return false;
  };
}

function defaultDormir(ms) {
  return new Promise((resolver) => setTimeout(resolver, ms));
}
