import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

/** HMAC-SHA256 del cuerpo crudo con el secreto compartido: 'sha256=<hex>' (cabecera X-Firma). */
export function firmar(secreto, cuerpo) {
  return 'sha256=' + createHmac('sha256', secreto).update(cuerpo).digest('hex');
}

/** Compara dos textos en tiempo constante (sin filtrar el largo del secreto). */
export function iguales(a, b) {
  const ha = createHash('sha256').update(String(a)).digest();
  const hb = createHash('sha256').update(String(b)).digest();
  return timingSafeEqual(ha, hb);
}

/** Verifica 'Authorization: Bearer <token>' en tiempo constante. */
export function tokenValido(cabecera, token) {
  if (!token || typeof cabecera !== 'string' || !cabecera.startsWith('Bearer ')) return false;
  return iguales(cabecera.slice('Bearer '.length), token);
}
