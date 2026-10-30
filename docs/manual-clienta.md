# Manual para la dueña del negocio

Esta guía es para ti, que atiendes a tus clientas. No necesitas saber de programación. Cuando un
paso diga **"pídele a quien administra el sistema"**, es porque toca el servidor; el resto lo
haces tú desde tu celular o desde tu Google Calendar.

## 1. ¿Qué hace el asistente?

- Contesta por WhatsApp (en tu mismo número) a quienes te escriben.
- Les enseña los servicios y precios, les muestra **solo las horas realmente libres** y agenda la cita
  en tu Google Calendar.
- Si dos personas piden la misma hora, a una le confirma y a la otra le ofrece otra hora. **Nunca
  agenda dos citas al mismo tiempo.**
- Responde con números cuando hace falta ("Escribe 1 para…"), para que sea fácil contestar.
- **No escribe primero a nadie.** Solo responde a quien le escribió.

## 2. Lo que haces una sola vez

### 2.1 Tener WhatsApp Business en tu celular
1. Instala **WhatsApp Business** desde la tienda de aplicaciones de tu celular.
2. Ábrelo con tu número de negocio y completa el perfil (nombre, foto, horario, dirección).
3. **Conserva esta app.** No tienes que cambiar de número ni de aplicación.

### 2.2 Vincular el asistente con un código QR
Es igual que abrir WhatsApp Web en un computador.
1. Pídele a quien administra el sistema que te muestre el código QR en una pantalla.
2. En tu celular abre WhatsApp Business → **Ajustes** (tres puntos) → **Dispositivos vinculados** →
   **Vincular un dispositivo**.
3. Escanea el código QR. En unos segundos aparece "Dispositivo vinculado" en tu lista.
4. Para comprobarlo, escríbele desde otro número a tu negocio y mira que el asistente responda.

> Si algún día el asistente deja de responder, puede que la vinculación se haya cerrado. Avisa a quien
> administra el sistema: probablemente haya que escanear el código de nuevo (paso 2.2).

### 2.3 Compartir tu Google Calendar con el asistente
1. Abre **Google Calendar** en un computador.
2. A la izquierda, junto al calendario que usas para tus citas, pulsa los tres puntos →
   **Configuración y uso compartido**.
3. Busca **"Compartir con personas o grupos específicos"** → **Agregar personas o grupos**.
4. Escribe el correo que te dé quien administra el sistema (termina en
   `iam.gserviceaccount.com`).
5. En **Permisos** elige **"Hacer cambios en los eventos"** → **Enviar**.
6. En la misma pantalla, más abajo, copia el **"ID de calendario"** y entrégaselo a quien administra
   el sistema (si usas tu calendario principal, es tu correo de Gmail).

Desde ese momento, todo lo que el asistente agenda aparece en tu calendario con el nombre del
servicio y el nombre de WhatsApp de la clienta, y su teléfono en la descripción.

## 3. Tu día a día

- **Agendar tú a mano:** crea el evento en Google Calendar como siempre. El asistente lo respeta y
  deja de ofrecer esa hora (puede tardar hasta unos minutos en enterarse; si alguien alcanza a
  pedir justo esa hora, el asistente lo verifica de nuevo antes de confirmar).
- **Responderle tú a una clienta:** escríbele desde tu celular como siempre. El asistente **se calla
  con esa clienta durante 2 horas** para no hablar encima de ti. Con las demás sigue funcionando.
- **Cancelar o mover una cita:** la clienta puede hacerlo escribiendo al asistente. Si prefieres
  hacerlo tú, borra o mueve el evento en el calendario y avísale por WhatsApp.
- **Horas que no quieres atender:** crea un evento en tu calendario (por ejemplo "Almuerzo" o
  "Vacaciones", puede ser de todo el día). Esas horas quedan bloqueadas.

## 4. Cambiar precios, servicios, horarios y domicilio

Estos datos están en archivos de configuración. **Pídele a quien administra el sistema** que los
cambie, diciéndole exactamente qué quieres. Así se ven por dentro, para que sepas qué pedir:

**Precios y servicios** (nombre, cuánto dura en minutos, precio en pesos y si se puede hacer a
domicilio):
```
- {id: semipermanente, nombre: "Semipermanente", duracion_min: 75, precio_cop: 50000, ...}
```
Los precios que trae el sistema son **de ejemplo**: revísalos antes de empezar a atender.

**Horario de atención** (hora de apertura y de cierre por día; "null" = ese día no atiendes):
```
lunes: {abre: "08:00", cierra: "18:00"}
domingo: null
```

**Descanso entre citas y anticipación:** cuántos minutos necesitas entre una cita y otra y con
cuántas horas de anticipación como mínimo se puede pedir una cita.

### Activar el servicio a domicilio y su recargo
Hay que decidir tres cosas y pedirle a quien administra el sistema que las configure:
1. **¿Atiendes a domicilio?** (sí/no).
2. **¿Cuánto cobras de recargo?** (en pesos; es obligatorio ponerlo si activas el domicilio).
3. **¿Cuánto tiempo te toma desplazarte?** (minutos; el asistente lo suma a la cita para que no se
   crucen).

Con el domicilio activo, el asistente le pregunta a la clienta si prefiere ir al local o que vayas
tú, le pide la dirección, le dice el valor total con el recargo y pone la dirección en el evento de
tu calendario. Algunos servicios (por ejemplo, las acrílicas) pueden quedar **solo en el local**.

> Después de cualquier cambio, quien administra el sistema debe reiniciarlo para que lo aplique.

## 5. Cuidar tu número de WhatsApp

WhatsApp puede bloquear números que se usan para mensajes masivos o automáticos. El asistente está
hecho para parecer una persona (tarda unos segundos en responder, muestra "escribiendo…", contesta
con un solo mensaje y tiene límites de mensajes por clienta). Para ayudar:
- **No** uses el asistente para escribirle primero a clientas ni para enviar promociones.
- **No** vincules tu número en otros programas de mensajes automáticos.
- Mantén tu celular con internet y la app actualizada.
- **Aviso honesto:** esta forma de conectarse no es la oficial de WhatsApp. Aunque se tomaron
  precauciones, **existe un riesgo de que WhatsApp restrinja el número**. Por eso conviene que tus
  clientas también puedan encontrarte por otro medio (llamada o Instagram).

## 6. Si algo no funciona

| Qué pasa | Qué hacer |
|---|---|
| El asistente no responde a nadie | Revisa que tu celular tenga internet; luego avisa a quien administra el sistema (puede ser la vinculación del paso 2.2) |
| Ofreció una hora que ya estaba ocupada | Avisa a quien administra; normalmente el sistema lo corrige solo al verificar antes de confirmar |
| Una cita no aparece en tu calendario | Avisa a quien administra con el nombre y la hora de la clienta |
| Quieres que el asistente no hable con alguien | Escríbele tú a esa clienta: se calla 2 horas |
| WhatsApp te avisa que tu número fue restringido | Deja de usar el asistente y escribe a quien administra el sistema de inmediato |
