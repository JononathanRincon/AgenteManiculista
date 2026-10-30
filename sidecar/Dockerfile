# syntax=docker/dockerfile:1
# Sidecar de WhatsApp (Baileys). Multi-stage, usuario no root, imagen base fijada.

FROM node:22.19.0-slim AS dependencias
# Baileys 6.7.x declara `libsignal` como dependencia de GitHub: npm necesita git para instalarla.
# El lockfile fija el commit exacto, así que la instalación es reproducible.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22.19.0-slim AS runtime
ENV NODE_ENV=production \
    AUTH_DIR=/data/baileys_auth \
    PORT=3000
WORKDIR /srv
COPY --from=dependencias /srv/node_modules ./node_modules
COPY package.json ./
COPY src ./src
# La sesión de WhatsApp vive en un volumen: reiniciar el contenedor no pide un QR nuevo.
RUN mkdir -p /data/baileys_auth && chown -R node:node /data
USER node
VOLUME ["/data/baileys_auth"]
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/estado',{headers:{authorization:'Bearer '+process.env.SIDECAR_TOKEN}}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "src/main.js"]
