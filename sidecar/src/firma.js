import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

/**
 * HMAC-SHA256 de `marca.cuerpo` con el secreto compartido: 'sha256=<hex>' (cabecera X-Firma).
 * La marca (segundos desde 1970, cabecera X-Marca) impide repetir un evento capturado.
 */
export function firmar(secreto, cuerpo, marca) {
  return 'sha256=' + createHmac('sha256', secreto).update(`${marca}.${cuerpo}`).digest('hex');
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
