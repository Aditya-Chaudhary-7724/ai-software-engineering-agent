# Production image for the Engineering Lab frontend (Next.js BFF).
#
# Build (from the repository root; .dockerignore keeps .env*, node_modules
# and .next out of the context):
#   docker build -t ai-swe-agent-frontend:local -f frontend/Dockerfile .
#
# No secret is needed at build time (every page renders per request) and
# none is baked in: OPERATOR_PASSWORD_HASH, SESSION_SECRET, API_SERVICE_TOKEN,
# DEMO_API_SERVICE_TOKEN, APP_ORIGIN, BACKEND_URL are runtime environment
# variables. Node 24.x: lib/crypto/argon2id.js needs node:crypto argon2
# (Node >= 24.7, package.json engines).

FROM node:24.19.0-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Standalone output: a minimal server.js with only the traced dependencies.
RUN NEXT_OUTPUT_STANDALONE=1 npm run build

FROM node:24.19.0-bookworm-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    # Listen on all interfaces INSIDE the container; docker-compose publishes
    # no port for it (only Caddy reaches it, on the internal network).
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# The official image's unprivileged `node` user (uid 1000).
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
# /login renders without a session or a backend call.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then((r) => process.exit(r.status < 500 ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "server.js"]
