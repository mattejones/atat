# ATAT web UI (Next.js). The browser only ever talks to this server: /api/* is proxied to the
# API container (see next.config.js), so the API itself is never published and no CORS or
# per-host API URL is needed.
#
# Next bakes the proxy target in at build time, hence the build arg. docker-compose.yml sets
# it to the API service name.
FROM node:20-alpine AS build
WORKDIR /web

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=${API_INTERNAL_URL} \
    NEXT_PUBLIC_API_URL=/api \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM node:20-alpine
WORKDIR /web
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=build /web/package.json /web/package-lock.json ./
COPY --from=build /web/node_modules ./node_modules
COPY --from=build /web/.next ./.next
COPY --from=build /web/public ./public
COPY --from=build /web/next.config.js ./

USER node
EXPOSE 18080

CMD ["npx", "next", "start", "-p", "18080"]
