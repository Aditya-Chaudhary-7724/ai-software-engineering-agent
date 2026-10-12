# Engineering Lab — frontend

Next.js (App Router, JavaScript) workspace for the AI Software Engineering
Agent. It is also the **backend-for-frontend**: the browser talks only to this
app; this app's server talks to the FastAPI backend with a service token the
browser never sees.

```
Browser ──(HttpOnly session cookie, same origin)──▶ Next.js server ──(Bearer API_SERVICE_TOKEN)──▶ FastAPI /api/v1
```

**Status (F5):** single-operator login, protected shell, dashboard,
repositories (add a GitHub repository by URL, live job progress, re-index),
file explorer, Ask (read-only Q&A with citations), knowledge graph, agent-trace
viewer. Agent tasks/approval and tests/evaluation are later phases and appear
in the navigation as disabled.

## Knowledge graph (F5)

- `/graph?repository=<id>[&node=<id>][&q=<text>]` — server-rendered: the
  backend returns one focus node and its one-hop neighbours (≤ 100 shown,
  truncation reported) or search results (≤ 20). Navigation is URL-driven
  (double-click a node, or select it and press **Focus**), so Back/Forward
  work and no browser-facing graph proxy route exists.
- Drawn with React Flow (`@xyflow/react`, MIT) using a deterministic layout
  (`lib/graph-layout.js`): incoming relationships on the left, outgoing on the
  right. Only nodes/edges the backend returned are drawn; there is no CALLS.
- Needs Neo4j and a graph built for the repository (re-index with
  “Rebuild the knowledge graph”); otherwise the page explains which is missing.

## Ask (F4)

- `/ask` — pick an indexed repository, ask a question. The browser posts
  `{ question }` to `/api/repositories/:id/ask`, guarded like a mutation
  (session + same origin + CSRF token) because it can spend LLM/embedding
  credits; only the trimmed question is forwarded to the backend.
- The answer is rendered as plain text (it is derived from untrusted
  repository content). Citations are the chunks the backend actually put in
  the model's context — each links to `/repositories/:id?path=…&lines=a-b#La`,
  which highlights exactly those lines.
- Every answer shows notices derived from the backend's response: stub LLM
  placeholder, non-semantic embeddings, graph not configured/unavailable,
  embedding-provider mismatch, or no relevant code. No scores are shown.
- Ask never edits files, runs tests, starts an agent, or touches GitHub.

## Repository ingestion (F3)

- The browser sends only `{ githubUrl, buildGraph }` (or a repository id to
  re-index) to this app's Route Handlers (`/api/ingestion-jobs`,
  `/api/repositories/:id/reindex`), which require a session, a same-origin
  request, and the CSRF token, then build the backend request from the
  validated values only — unknown fields are never forwarded.
- The backend clones into its own `WORKSPACE_ROOT` and runs the job in a
  background worker; the page polls `/api/ingestion-jobs/:id` every 2 s
  (backing off on errors, stopping at `completed`/`failed`).
- Progress is shown as the stages the backend reports — no estimated
  percentages. Graph building shows whether it was requested and its outcome.
- Requires the backend to have `WORKSPACE_ROOT` set and its schema applied
  (`backend/scripts/apply_schema.py`); otherwise the form explains that
  ingestion is unavailable.

## Requirements

- Node.js **>= 24.7** (uses the built-in `crypto.argon2`; see "Argon2id" below)
- The backend running with `API_SERVICE_TOKEN` set (see `../.env.example`)

## Secure local setup

```bash
cd frontend
npm ci

# 1. Create your local, gitignored env file from the template.
cp .env.example .env.local

# 2. Operator password hash. Input is hidden; the password is never an argv
#    value (shell history / `ps`) and is never printed.
npm run hash-password            # paste the printed OPERATOR_PASSWORD_HASH=... line into .env.local

# 3. Session secret.
npm run -s generate-secret       # put the output in SESSION_SECRET=

# 4. Service token shared with the backend (same value on both sides).
npm run -s generate-secret       # put it in frontend/.env.local AND the backend's .env as API_SERVICE_TOKEN

# 5. Run the backend (repository root), then this app.
#    (backend, from the repository root; WORKSPACE_ROOT must be absolute)
#      export WORKSPACE_ROOT="$PWD/data/workspaces"
#      .venv/bin/python backend/scripts/apply_schema.py
#      cd backend && ../.venv/bin/uvicorn api.main:app --port 8000
npm run dev                      # http://127.0.0.1:3000
```

Never commit `.env.local`; it is covered by the repository's `.gitignore`
(`.env.*`). There is no default password or secret anywhere — until
`OPERATOR_PASSWORD_HASH` and `SESSION_SECRET` are valid, sign-in is disabled.

## Environment variables (all server-only)

| Variable | Required | Purpose |
|---|---|---|
| `OPERATOR_PASSWORD_HASH` | yes | base64url-wrapped Argon2id PHC hash (`npm run hash-password`) |
| `SESSION_SECRET` | yes | >= 32 chars; signs sessions, derives CSRF tokens; rotate to revoke all sessions |
| `SESSION_MAX_AGE_SECONDS` | no | absolute session lifetime, default 28800 (8 h), 300..604800 |
| `APP_ORIGIN` | production | exact public origin; required when `NODE_ENV=production` |
| `TRUST_PROXY_HEADERS` | no | `true` only behind a proxy that overwrites `X-Forwarded-For` |
| `BACKEND_URL` | yes | FastAPI base URL, reachable from this server |
| `API_SERVICE_TOKEN` | yes | must equal the backend's; sent only server-to-server |

## Authentication design

- **Password:** Argon2id (m = 64 MiB, t = 3, p = 1), verified in the
  `/api/auth/login` Route Handler. Parameters read from configuration are
  bounded, so a malformed hash cannot trigger a memory/CPU blow-up.
- **Session:** stateless HMAC-SHA256-signed token in a cookie that is
  `HttpOnly`, `SameSite=Strict`, `Path=/`, and in production `Secure` with the
  `__Host-` prefix. A new session id on every login. Absolute expiry.
  Logout deletes the cookie; because tokens are stateless, a *copied* token
  stays valid until expiry — rotate `SESSION_SECRET` to revoke everything.
- **Two gates:** `proxy.js` (Next 16's renamed middleware) redirects or 401s
  early; the Data Access Layer (`lib/server/dal.js`) verifies again in every
  protected layout and before every backend call, so a proxy bypass alone
  exposes nothing.
- **CSRF:** `SameSite=Strict` cookie + same-origin check (`Origin` /
  `Sec-Fetch-Site`) + a session-bound token (`x-csrf-token` header) for
  authenticated state-changing requests. Future mutation routes call
  `requireAuthenticatedMutation(request)` (`lib/server/auth-handlers.js`).
- **Rate limiting:** 5 failed logins / 15 min per client, 30 / 15 min
  globally, at most 4 concurrent verifications; checked before hashing.
  In-memory, per process — see limitations.
- **Errors:** fixed codes only (`invalid_credentials`, `too_many_attempts`,
  `auth_not_configured`, …); which setting is broken is logged server-side only.
- **No secrets in the browser:** no `NEXT_PUBLIC_` variables; every
  `lib/server/*` module imports `server-only`, so a client import fails the
  build. Tests guard both properties.

## Argon2id runtime

Uses Node's built-in `crypto.argon2` (no native addon, no WASM dependency).
If a deployment target cannot run Node >= 24.7, swap `lib/crypto/argon2id.js`
to `@node-rs/argon2` — the stored PHC format is the same standard.

## Tests

```bash
npm test          # Vitest: auth, session, CSRF, rate limiting, proxy, backend client, guards
npm run build     # production build; every workspace route must be ƒ (dynamic)
```

## Known limitations

- Rate-limit state is per process and resets on restart; a multi-instance
  deployment needs a shared store.
- Stateless sessions cannot be individually revoked before expiry.
- Full nonce-based Content-Security-Policy is scheduled for F8; baseline
  headers (`frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, …) are set now.
