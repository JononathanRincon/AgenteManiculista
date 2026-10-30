// Memoria acotada de lo que este sidecar envió: sirve para distinguir los mensajes del bot de los
// que la dueña escribe a mano (intervención humana) y para no duplicar envíos reintentados.
export class Emitidos {
  constructor(maximo = 1000) {
    this.maximo = maximo;
    this.ids = new Set();
    this.claves = new Map();
  }

  registrar(id) {
    this.ids.add(id);
    while (this.ids.size > this.maximo) this.ids.delete(this.ids.values().next().value);
  }

  fueEmitido(id) {
    return this.ids.has(id);
  }

  recordarClave(clave, id) {
    this.claves.set(clave, id);
    while (this.claves.size > this.maximo) this.claves.delete(this.claves.keys().next().value);
  }

  idDeClave(clave) {
    return this.claves.get(clave) ?? null;
  }
}
