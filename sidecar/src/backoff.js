// Reconexión con backoff exponencial: 2, 4, 8, 16, 32 y luego 60 s (tope), más jitter de 0 a 1 s.
export const TOPE_SEGUNDOS = 60;

/** Espera en milisegundos antes del intento `intento` (1, 2, 3...). */
export function esperaReconexionMs(intento, azar = Math.random) {
  const base = Math.min(TOPE_SEGUNDOS, 2 ** Math.max(1, intento));
  return Math.round((base + azar()) * 1000);
}
