# syntax=docker/dockerfile:1

# Stages:
#   deps    -> installs npm packages (cached until package-lock.json changes)
#   builder -> runs "next build" (output: "standalone")
#   runner  -> tiny final image: just the standalone server + static files (the default target)
#
# Debian "slim", not Alpine: Tailwind v4 and Next ship native binaries, and the Debian (glibc)
# builds are the safest choice.
ARG NODE_VERSION=22

# ---------------------------- deps ------------------------------------ 
FROM node:${NODE_VERSION}-bookworm-slim AS deps
WORKDIR /app
# Only the manifests are copied first, so this slow layer is reused until they change.
COPY package.json package-lock.json ./
# "npm ci" installs exactly what the lockfile says (fails if the lockfile is out of date).
# The cache mount keeps downloaded packages between builds (BuildKit).
RUN --mount=type=cache,target=/root/.npm npm ci

# ----------------------- builder ----------------------------------------- 
FROM node:${NODE_VERSION}-bookworm-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# The browser calls the API at this address. It is PUBLIC (it ends up in the JavaScript
# bundle), so never put a secret in a NEXT_PUBLIC_* variable.
ARG NEXT_PUBLIC_API_URL=http://localhost:8001
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .
# The runner stage copies ./public. A project without that folder would fail the copy.
RUN mkdir -p public
RUN npm run build
# Fail the build here, with a clear message, if standalone output is missing.
RUN test -f .next/standalone/server.js \
    || (echo 'ERROR: .next/standalone/server.js missing. Set output: "standalone" in next.config.ts' && exit 1)

# -------------------------- runner -------------------------------------- 
FROM node:${NODE_VERSION}-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root, numeric id (Kubernetes' runAsNonRoot can verify a number), no shell, no home.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

# Files stay owned by root and read-only for appuser: the app cannot rewrite its own code.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER 10001
EXPOSE 3000

# /login returns 200 without needing the backend ("/" would answer with a redirect).
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]