FROM node:24-slim AS build
WORKDIR /build/apps/web
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM node:24-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN groupadd --gid 10001 counterforge && useradd --uid 10001 --gid 10001 --create-home counterforge
COPY --from=build --chown=counterforge:counterforge /build/apps/web/.next/standalone ./
COPY --from=build --chown=counterforge:counterforge /build/apps/web/.next/static ./.next/static
USER counterforge
EXPOSE 3000
CMD ["node", "server.js"]
