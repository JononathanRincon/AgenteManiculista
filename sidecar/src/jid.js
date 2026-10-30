// Solo se atienden chats individuales: grupos (@g.us), difusión (status@broadcast),
// canales (@newsletter) e ids opacos (@lid) se ignoran por completo (EARS-WA-15).
export const SUFIJO_INDIVIDUAL = '@s.whatsapp.net';

/** '573001112233:12@s.whatsapp.net' -> '573001112233'; null si no es un chat individual válido. */
export function telefonoDeJid(jid) {
  if (typeof jid !== 'string' || !jid.endsWith(SUFIJO_INDIVIDUAL)) return null;
  const numero = jid.slice(0, -SUFIJO_INDIVIDUAL.length).split(':', 1)[0];
  return /^\d{6,20}$/.test(numero) ? numero : null;
}

export function esChatIndividual(jid) {
  return telefonoDeJid(jid) !== null;
}
