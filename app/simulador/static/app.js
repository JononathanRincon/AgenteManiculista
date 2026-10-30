"use strict";

const TELEFONO = "573000000001";
const el = (id) => document.getElementById(id);
let ocupado = false;
let mensajesVistos = 0;
const citasVistas = new Set();

async function api(ruta, opciones = {}) {
  const respuesta = await fetch(ruta, {
    headers: { "Content-Type": "application/json" },
    ...opciones,
  });
  if (respuesta.status === 204) return null;
  if (!respuesta.ok) throw new Error("Error " + respuesta.status);
  return respuesta.json();
}

function crear(etiqueta, clase, texto) {
  const nodo = document.createElement(etiqueta);
  if (clase) nodo.className = clase;
  if (texto !== undefined) nodo.textContent = texto;
  return nodo;
}

function pintarChat(mensajes) {
  const caja = el("mensajes");
  const alFinal = caja.scrollHeight - caja.scrollTop - caja.clientHeight < 80;
  caja.replaceChildren();
  const ultimo = mensajes.length - 1;
  const previos = mensajesVistos;
  mensajesVistos = mensajes.length;
  mensajes.forEach((m, i) => {
    const burbuja = crear("div", "burbuja " + (m.de === "bot" ? "bot" : "clienta") + (i >= previos ? " nueva" : ""));
    burbuja.appendChild(crear("div", "", m.cuerpo));
    if (m.opciones && m.opciones.length) {
      const lista = crear("div", "opciones");
      m.opciones.forEach((o) => {
        const boton = crear("button", "opcion");
        boton.type = "button";
        boton.textContent = o.titulo;
        if (o.descripcion) boton.appendChild(crear("small", "", o.descripcion));
        boton.disabled = i !== ultimo;
        boton.addEventListener("click", () => enviar(null, o.id));
        lista.appendChild(boton);
      });
      burbuja.appendChild(lista);
    }
    caja.appendChild(burbuja);
  });
  if (alFinal) caja.scrollTop = caja.scrollHeight;
}

function horaCorta(iso) {
  return iso.slice(11, 16);
}

function nombreDia(iso) {
  const fecha = new Date(iso.slice(0, 10) + "T12:00:00");
  const texto = fecha.toLocaleDateString("es-CO", { weekday: "long", day: "numeric", month: "long" });
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

function pintarAgenda(eventos) {
  const caja = el("agenda");
  caja.replaceChildren();
  if (!eventos.length) {
    caja.appendChild(crear("p", "vacio", "Sin citas todavía. Agenda una desde el chat."));
    return;
  }
  const porDia = new Map();
  eventos.forEach((e) => {
    const dia = e.inicio.slice(0, 10);
    if (!porDia.has(dia)) porDia.set(dia, []);
    porDia.get(dia).push(e);
  });
  porDia.forEach((lista, dia) => {
    const seccion = crear("section", "dia");
    seccion.appendChild(crear("h3", "", nombreDia(dia)));
    lista.forEach((e) => {
      const clave = e.id + e.estado;
      const fila = crear("div", "cita " + e.estado + (citasVistas.has(clave) ? "" : " nueva"));
      citasVistas.add(clave);
      fila.appendChild(crear("span", "hora", horaCorta(e.inicio) + "–" + horaCorta(e.fin)));
      fila.appendChild(crear("span", "", e.titulo + (e.estado === "apartada" ? " · confirmando…" : "")));
      seccion.appendChild(fila);
    });
    caja.appendChild(seccion);
  });
}

async function refrescar() {
  try {
    const [chat, agenda] = await Promise.all([
      api("/demo/api/chat/" + TELEFONO),
      api("/demo/api/calendario"),
    ]);
    pintarChat(chat.mensajes);
    pintarAgenda(agenda.eventos);
  } catch (error) {
    console.error(error);
  }
}

async function enviar(texto, botonId) {
  if (ocupado) return;
  ocupado = true;
  try {
    const cuerpo = { telefono_demo: TELEFONO };
    if (botonId) cuerpo.boton_id = botonId;
    else cuerpo.texto = texto;
    await api("/demo/api/mensaje", { method: "POST", body: JSON.stringify(cuerpo) });
  } catch (error) {
    console.error(error);
  } finally {
    ocupado = false;
    await refrescar();
  }
}

function mostrarBanner(partes) {
  const banner = el("banner");
  banner.replaceChildren(...partes);
  banner.hidden = false;
}

async function carrera() {
  const boton = el("btn-carrera");
  boton.disabled = true;
  try {
    const r = await api("/demo/api/carrera", { method: "POST", body: JSON.stringify({}) });
    mostrarBanner([
      crear("span", "ok", "Cita confirmada: " + r.ganadora.nombre),
      document.createTextNode("  ·  "),
      crear("span", "no", "Rechazada: " + r.rechazada.nombre + " — " + r.rechazada.mensaje),
    ]);
  } catch (error) {
    mostrarBanner([document.createTextNode("No se pudo correr la prueba. Intenta de nuevo.")]);
  } finally {
    boton.disabled = false;
    await refrescar();
  }
}

async function reiniciar() {
  await api("/demo/api/reiniciar", { method: "POST" });
  mensajesVistos = 0;
  citasVistas.clear();
  el("banner").hidden = true;
  await refrescar();
}

function conectarEventos() {
  let pendiente = null;
  const fuente = new EventSource("/demo/api/eventos");
  fuente.addEventListener("cambio", () => {
    clearTimeout(pendiente);
    pendiente = setTimeout(refrescar, 120);
  });
}

el("tel-visible").textContent = "+" + TELEFONO;
el("formulario").addEventListener("submit", (evento) => {
  evento.preventDefault();
  const campo = el("texto");
  const texto = campo.value.trim();
  if (!texto) return;
  campo.value = "";
  enviar(texto, null);
});
el("btn-carrera").addEventListener("click", carrera);
el("btn-reiniciar").addEventListener("click", reiniciar);
refrescar();
conectarEventos();
