# Deployment

This document describes the ACTUAL state of this project's production readiness, as audited and built in Phase 15 — not an aspirational architecture. Every claim is labeled as one of:

- **IMPLEMENTED AND TESTED** — real code, run for real in this environment, backed by a test or a command whose output is shown/reproducible.
- **READY TO DEPLOY** — a concrete artifact exists (a Dockerfile, a script, a config) that has been built/run locally, but has not been deployed to any actual hosting platform.
- **DOCUMENTED TARGET ARCHITECTURE** — a reasoned design for something not yet built, so the gap is explicit rather than silently assumed.

No cloud deployment was performed in this phase. No deployment metrics are invented anywhere below.

**F8 (production-readiness review, 2026-11-07):** sections 1, 3, 11, 12, 15 and 19–22 were rewritten because they still described the Phase 15 state ("no frontend exists", "the API only serves health"), which F1–F6 made false. Other sections keep their Phase 15 text plus a dated **Update (F8)** note. Section 23 summarises the F8 review. Still true: **nothing has been deployed to any hosting platform.**

## 1. Production Architecture

```
Operator's browser
   │  HTTPS (TLS terminated by the platform / reverse proxy)
   ↓
Engineering Lab — Next.js backend-for-frontend (frontend/)
   │  operator login (Argon2id hash) · signed session cookie · CSRF · nonce CSP
   │  SERVER-side only: Authorization: Bearer API_SERVICE_TOKEN
   ↓  (private network — the browser never calls the API)
Backend API — FastAPI (backend/api/) · /api/v1 + /health/*
   │  in-process workers: ingestion (git clone + index), agent tasks (bound approval)
   ├──→ PostgreSQL + pgvector  (REQUIRED: chunks, jobs, agent_tasks, LangGraph checkpoints)
   ├──→ Neo4j                  (OPTIONAL: knowledge graph)
   ├──→ Anthropic / OpenAI     (OPTIONAL: real LLM / embeddings; stand-ins without keys)
   ├──→ GitHub over HTTPS      (public clones; GITHUB_TOKEN only for private repos)
   └──→ Docker sandbox         (tests of untrusted code — NOT available inside the
                                API image; see section 7)
```

Status: every box is IMPLEMENTED AND TESTED locally; none is deployed.

## 2. Local Development vs Production

| | Local development | Production |
|---|---|---|
| Backend process | `uvicorn api.main:app --reload` or direct script invocation (`manual_agent_demo.py`, etc.) | `gunicorn api.main:app --worker-class uvicorn.workers.UvicornWorker` inside `backend/Dockerfile` (no `--reload`) |
| PostgreSQL | Homebrew, local, trust-auth | A managed/provisioned instance with real credentials over `DATABASE_URL` |
| Neo4j | Local Docker container (`docker run neo4j:5-community`) | A managed/provisioned instance (e.g. Neo4j Aura) or self-hosted with real credentials |
| Embeddings/LLM | `DeterministicLocalEmbeddingProvider`/`StubLLMProvider` (no credentials) | `OpenAIEmbeddingProvider`/`AnthropicLLMProvider` (real API keys) |
| Logging | Plain console output | Structured JSON (`APP_ENV=production` triggers `configure_json_logging()`) |
| API docs | `/docs`, `/redoc` enabled | Disabled (`APP_ENV=production`) |
| Secrets | `.env` (gitignored, local-only) | Injected by the deployment platform's secret manager — never a file in the image |

## 3. Frontend Deployment

**Status: READY TO DEPLOY (built and run locally with `next build` + `next start`); not deployed.**

`frontend/` is a Next.js 16 app acting as a backend-for-frontend. Its server holds every credential; the browser only ever holds an HttpOnly session cookie. Production needs:

| Variable | Notes |
|---|---|
| `OPERATOR_PASSWORD_HASH` | Argon2id hash from `npm run -s hash-password` — never a plaintext password |
| `SESSION_SECRET` | ≥ 32 chars (`npm run -s generate-secret`); rotating it signs every session out |
| `APP_ORIGIN` | **Required when `NODE_ENV=production`**: the public origin, e.g. `https://lab.example.com`. Pins the CSRF origin check and defeats DNS rebinding. An `https://` origin also turns on HSTS and `upgrade-insecure-requests` |
| `BACKEND_URL` | Where the frontend SERVER reaches the API (private address) |
| `API_SERVICE_TOKEN` | Same value as the backend's; server-only |
| `SESSION_MAX_AGE_SECONDS` | Optional, 300–604800, default 8 h |
| `TRUST_PROXY_HEADERS` | `true` **only** behind a proxy that overwrites `X-Forwarded-For`; otherwise login rate limiting uses one shared bucket |

None of these uses the `NEXT_PUBLIC_` prefix, so none is inlined into browser JavaScript. F8 checked the built `.next/static` output: none of the secret names appears.

Run: `npm ci && npm run build && npm start` (Node ≥ 24.7). `npm start` is `NODE_ENV=production`, so sign-in stays disabled until `APP_ORIGIN` is set (see `frontend/README.md`). Serve it behind HTTPS: the session cookie is `__Host-`-prefixed and `Secure` in production, and browsers only accept that over HTTPS or on `localhost`/`127.0.0.1`.

**No frontend container image exists.** Use the platform's Node runtime (e.g. Vercel or any Node host), or add a Dockerfile when a container platform is actually chosen.

**Security headers (F8):** every page and API route gets a per-request nonce Content-Security-Policy (`script-src 'self' 'nonce-…' 'strict-dynamic'`, no `unsafe-inline` for scripts; `style-src 'unsafe-inline'` because a few components use style attributes). Production adds HSTS (`max-age=31536000`) when `APP_ORIGIN` is https. Tested by `frontend/tests/security-headers.test.js`, and against a real `next start` (2026-11-08) on every page route, authenticated with a throwaway session secret: `/`, `/login`, `/tasks`, `/traces`, `/graph`, `/ask`, `/repositories` and both 404 paths. Every `<script>` carried that response's nonce (11–13 per page); `next dev` gave 19/19 plus `'unsafe-eval'`.

That check found one incompatibility, now fixed: Next.js **prerendered the built-in 404 as static HTML**, so its 11 scripts had no nonce and were blocked. `app/layout.js` now calls `connection()`, rendering every page per request (all other pages already were). Remaining static page: Next's `_global-error.html`, shown only if the root layout itself throws; its scripts (the "try again" control) would be blocked, but its message still renders.

**Verified at the HTTP level only.** Re-checked 2026-11-09 against a real `next start`: the CSP header is on 200, redirect and 401 responses, a fresh nonce per request, 12/12 `<script>` tags on `/login` carry that nonce, no `x-middleware-*` header reaches the client, no HSTS over plain http. **Not yet checked in a real browser** for CSP console violations (the connected Chrome could not reach this machine's loopback, `ERR_CONNECTION_REFUSED`). That remains outstanding: do it once before relying on the policy.

## 4. Backend Deployment

**Status: READY TO DEPLOY** (built and verified locally; not deployed to any hosting platform).

`backend/api/main.py` — a FastAPI application, deliberately minimal (see its own module docstring): `/`, `/health/live`, `/health/ready`. It does **not** yet expose the agent's operations (asking a question, approving a change, triggering an evaluation run, cloning a GitHub repo) as HTTP endpoints. This is a deliberate scope decision, not an oversight: Phase 15's job is production INFRASTRUCTURE (health checks, containerization, secrets, CI, observability wiring), and building a full, carefully-authorized REST surface for consequential agent operations (which must preserve every Phase 14 approval/authorization gate) is substantial, separate application-layer work that deserves its own dedicated phase, not something to bolt on hastily under a deployment-focused phase. The existing `AgentService`/`GitHubIntegrationService`/`EvaluationSuite` are already fully built, tested, and directly reusable by whatever future endpoint layer adds this — nothing about this phase's work would need to change.

**Production configuration built this phase:**
- **Debug/development settings**: `APP_ENV=production` disables interactive API docs (`/docs`, `/redoc`, `/openapi.json` — a minor information-disclosure surface, closed) and switches to structured JSON logging.
- **CORS**: `CORSMiddleware` is wired up but OFF by default — `ALLOWED_ORIGINS` (comma-separated exact origins) is empty unless explicitly configured, meaning no CORS headers are sent at all until a real frontend origin exists. Never a wildcard.
- **Request size limits / timeouts**: NOT implemented as application middleware — this is the correct, standard pattern for an ASGI app: Gunicorn's `--timeout` (set in `backend/Dockerfile`) and a fronting reverse proxy/load balancer's own body-size limit are where this belongs, not hand-rolled middleware duplicating what the server/infra layer already does.
- **Structured logging**: reuses Phase 13's `observability.logging_config.configure_json_logging()` — no second logging system.
- **Error handling**: a global exception handler ensures an unhandled exception NEVER returns a stack trace, exception message, file path, or credential in the HTTP response body — only a fixed `{"error": "internal_server_error"}` — while the full (still Phase 13/14-redacted) detail goes to the server-side structured log. Regression-tested (`tests/api/test_main.py::test_unhandled_exception_never_leaks_internal_detail_in_the_response`) by making a route raise an exception whose message deliberately contains a fake secret and a fake file path, and confirming neither appears in the response.
- **Graceful shutdown**: handled by Gunicorn's own SIGTERM handling (finishes in-flight requests, then exits) — no custom code needed since the app holds no long-lived global connections (every service call opens/closes its own).
- **Worker configuration**: Gunicorn + `uvicorn.workers.UvicornWorker`, the standard documented FastAPI production pairing. The worker count is Gunicorn's `WEB_CONCURRENCY` environment variable, **1 when unset**. **Corrected (2026-11-12):** this line used to say "`--workers 2` by default, overridable via `WEB_CONCURRENCY`", which was false: an explicit `--workers` flag overrides `WEB_CONCURRENCY`, so the variable was ignored (verified on a real container: `WEB_CONCURRENCY=1` still ran 2 workers). The CMD no longer passes `--workers`; `tests/test_dockerfile.py` resolves the count with Gunicorn's own parser and guards against the flag returning. Verified on the rebuilt image (no database configured, so the in-process workers were not started; memory under real load is higher and not measured): unset → 1 worker, 153 MiB idle; `WEB_CONCURRENCY=3` → 3 workers, 406 MiB idle; the old CMD → 2 workers, 276 MiB idle.
- **Startup checks**: the `lifespan` context manager configures logging and logs a startup line; it does NOT run schema migrations or any mutating database operation on boot (see section 15 — migrations are a separate, explicit step).

**Containerization** (`backend/Dockerfile`) — built and run locally in this session:
- `python:3.11-slim` base (minimal, official, actively maintained).
- Deterministic install: pinned `backend/requirements.txt`, `--no-cache-dir`.
- Runs as a non-root user (`appuser`, uid 1000) — **verified**: `docker exec ... whoami` → `appuser`; `id` → `uid=1000(appuser) gid=1000(appuser)`.
- No secrets baked in — **verified**: `docker run --rm ... find /app -iname '*.env*'` returns nothing; `docker history --no-trunc` shows no credential value in any layer; the image's own `Config.Env` contains only build-time Python flags, nothing runtime-secret-shaped.
- `HEALTHCHECK` against `/health/live` (liveness only, matching the liveness/readiness split — an orchestrator's own readiness probe should hit `/health/ready` separately with its own retry policy).
- `CMD` runs Gunicorn with the Uvicorn worker class, no `--reload`.
- `.dockerignore` (project root) excludes `.env`/`.git`/`.venv`/`tests`/`docs` from the build context entirely — defense in depth beyond the Dockerfile's own `COPY` instructions never referencing them.
- **Update (frontend phase F3): `git` added.** Repository ingestion runs in an in-process worker inside the API process, and `github_integration/git_operations.py` clones with the `git` CLI, so the image now installs `git` via `apt-get install --no-install-recommends git` (package lists removed in the same layer). Only git's hard dependencies come with it (Perl, libcurl/GnuTLS, Kerberos/LDAP client libraries) — no `ssh` client, no recommended extras. Cost: image size 502 MB → 647 MB. Verified on the built image: `git version 2.47.3`; still runs as `appuser` (uid 1000); healthcheck and Gunicorn command unchanged; no `.env` files, no Docker CLI, no ssh, no global/system git config. Verified end to end with a real container (`WORKSPACE_ROOT=/app/workspaces`, writable by `appuser`, against the test PostgreSQL via `host.docker.internal`): the worker cloned and indexed `pallets/markupsafe` (124 chunks) and `octocat/Hello-World`; a nonexistent repository failed with `github_repository_not_found`; hostile URLs/extra fields were rejected (422); the clones' git config contains no credentials. Unchanged by this: URL validation (`parse_github_url`), workspace confinement (`resolve_clone_destination`), and the HTTP surface (no push/PR endpoint exists). `GITHUB_TOKEN`, if set, still reaches git only through the child process environment. In production, mount a persistent volume at the `WORKSPACE_ROOT` path, writable by uid 1000.

**Verified locally, this session** (see section 21 for full output):
```
docker build -t ai-swe-agent-backend:latest -f backend/Dockerfile .
docker run -d -p 8200:8000 -e DATABASE_URL=... -e APP_ENV=production ai-swe-agent-backend:latest
curl http://127.0.0.1:8200/health/live   → {"status": "alive"}
curl http://127.0.0.1:8200/health/ready  → {"ready": true, "dependencies": [...]}  (real Postgres reached via host.docker.internal)
```

**Update (F8): the API surface.** The opening paragraph of this section is out of date. `/api/v1` now exists (F1–F6): capabilities, repositories, files, graph, traces, ingestion jobs, Ask, and agent tasks with bound approval. It is protected by `API_SERVICE_TOKEN` at the router (fail closed with 503 when unset or short), and every route is enumerated by `tests/api/test_v1_auth.py`. **Keep the API on a private network:** its only intended caller is the frontend server.

F8 changes:
- `Cache-Control: no-store` / `nosniff` are decided on the raw ASGI path, not on `request.url`. The pinned Starlette rebuilds `request.url` from the client's Host header (PYSEC-2026-161/-248), and a forged Host previously stripped `no-store` from source-code responses (auth was never affected): `tests/api/test_f8_header_hardening.py`.
- When CORS is enabled, it allows only `Authorization` and `Content-Type` headers.
- `redirect_slashes=False`: Starlette's automatic trailing-slash redirect built its `Location` from the client's Host header, and it fired before authentication (`Host: evil.example` → `Location: http://evil.example/api/v1/...`). A trailing slash is now a plain 404 (or 401). Every route and every frontend call uses exact paths (checked).

Gunicorn runs `WEB_CONCURRENCY` worker processes (1 by default), and **each starts its own ingestion and agent worker threads**. That is safe: claims use `FOR UPDATE SKIP LOCKED` (tested). A task running when its process stops is marked `interrupted` by the next stale-heartbeat recovery; it is never resumed or reported as successful. Set `AGENT_WORKER_ENABLED=false` / `INGESTION_WORKER_ENABLED=false` on processes that should only serve HTTP.

## 5. PostgreSQL / pgvector

**Status: IMPLEMENTED AND TESTED locally; production hosting is a DOCUMENTED TARGET.**

- **Connection**: `vectorstore.store.VectorStore` opens a fresh `psycopg.connect(database_url)` per call — no pooling. **Audited, not changed this phase**: adding a pool (`psycopg_pool.ConnectionPool`, the officially recommended psycopg3 extra) is a reasonable production improvement, but rewiring Phase 3's connection model this late, without evidence of actual connection exhaustion from a real deployment, would be exactly the kind of speculative change this phase's own instructions warn against ("avoid introducing breaking changes," "do not add unnecessary infrastructure"). Documented here as a concrete, scoped follow-up, not implemented.
- **Migrations**: no framework (Alembic) — `vectorstore/schema.sql` applied idempotently via `vectorstore.migrations.apply_schema` (`CREATE ... IF NOT EXISTS` only, never destructive). **Phase 15 adds `backend/scripts/apply_schema.py`** as the explicit, single, deliberate migration step (previously this only ran implicitly as a side effect of the first `IndexingService.index_repository()` call) — run once per deploy, not hidden in application startup. Verified this session: running it twice in a row is a no-op both times, and it validates required configuration first via `backend/config.py`.
- **Indexes** (already present, Phase 3, unchanged): a `hnsw` index on `embedding` for cosine similarity search, a `gin` index on `to_tsvector('english', content)` for keyword search, plus ordinary btree indexes on `repository_id`/`language`/`chunk_type`. These are real, already-applied production-appropriate indexes, not something this phase invented.
- **Backup strategy — DOCUMENTED TARGET, not implemented**: no backup automation exists for the local dev database (a throwaway `ai_swe_agent`/`ai_swe_agent_test` pair). For a real production deployment: a managed Postgres offering's automated snapshot/point-in-time-recovery feature (RDS, Cloud SQL, Neon, Supabase, ...) is the recommended target — this project's schema is small and standard SQL, so it needs nothing bespoke, just a provider with automated backups turned on. A manual fallback (`pg_dump`) is always available and requires no project-specific tooling.
- **Restore strategy — DOCUMENTED TARGET**: `pg_restore` against a backup, then `backend/scripts/apply_schema.py` to ensure any newer indexes exist (idempotent, safe to run against a freshly-restored database).
- **Credentials / TLS**: `DATABASE_URL` already supports `sslmode=require` (a standard `psycopg`/libpq connection-string parameter) — not hardcoded anywhere in this codebase, so enabling it in production is a configuration change (setting `DATABASE_URL` accordingly), not a code change.
- **No destructive operation was run against any existing database in this phase.** `apply_schema.py` was tested against this project's own local dev database (already containing this schema) and confirmed to be a safe no-op.

## 6. Neo4j

**Status: IMPLEMENTED AND TESTED locally (Docker container); production hosting is a DOCUMENTED TARGET, explicitly not claimed as deployed.**

Local development uses a plain `docker run neo4j:5-community` container with no persistence volume configured beyond the container's own filesystem (data is lost if the container is removed) and no TLS (`bolt://`, not `bolt+s://`). This is adequate for local development and was never claimed to be production-ready — restating that explicitly here, since Phase 14's own audit already noted Neo4j's local setup should not be mistaken for a production one.

**Target for production**: a managed offering (Neo4j Aura) is the practical choice — it handles persistence, backups, and TLS termination without this project needing to operate a stateful database server itself, consistent with this project's own "don't add infrastructure without a real reason" policy (operating a self-hosted, backed-up, TLS-terminated graph database is real infrastructure burden with no benefit over a managed option at this project's scale). If self-hosting is genuininely required instead: a persistent volume for `/data`, `bolt+s://` with a real certificate, and a backup schedule (`neo4j-admin database dump`) would all be needed — none of this exists today, and this document does not claim otherwise.

**Connection/readiness behavior** (built this phase): `backend/api/health.py` treats Neo4j as OPTIONAL — its absence or unavailability is reported in `/health/ready`'s response but does NOT flip overall readiness to false, matching `backend/config.py`'s own REQUIRED/OPTIONAL classification and this phase's explicit instruction that "a temporary Neo4j outage should not necessarily make the entire process appear dead." Verified with three real scenarios (Neo4j unset, Neo4j set but unreachable, Neo4j reachable) — see `tests/api/test_health.py`.

**Update (F8):** the local container is `ai-swe-agent-neo4j` with a named volume `ai_swe_agent_neo4j_data` on `/data`, so data survives container removal (the text above predates it). It has `restart=no`, which is why it is often found stopped. Start it with `docker start ai-swe-agent-neo4j`. Equivalent fresh setup:

```bash
docker run -d --name ai-swe-agent-neo4j -p 127.0.0.1:7474:7474 -p 127.0.0.1:7687:7687 \
  -v ai_swe_agent_neo4j_data:/data -e NEO4J_AUTH=neo4j/<your-password> --restart unless-stopped neo4j:5-community
```

Binding to `127.0.0.1` keeps it off the LAN. **The current container is exposed on the LAN**: it publishes on all interfaces, protected only by its password and without TLS. Neo4j Browser answered on this Mac's LAN address on 2026-11-08 and again on 2026-11-09 (HTTP 200). **It must be rebound to loopback before this machine is used on an untrusted network** (cafés, conferences, shared Wi-Fi). Not applied yet, deliberately: the restart policy and the loopback recreation will be done together after a reviewed backup and rollback plan.

**Recorded configuration of the existing container (2026-11-08, before any change):** name `ai-swe-agent-neo4j`; image `neo4j:5-community`, local image ID `sha256:3388e05ee53c…` (Neo4j 5.26.30); created 2026-09-20; network `bridge`; ports `7474/tcp` and `7687/tcp` published on all interfaces (IPv4 and IPv6); restart policy `no`; not privileged, no added capabilities, no memory/CPU limits. Mounts: named volume `ai_swe_agent_neo4j_data` → `/data` (518 MB, owned by uid 7474), and an **anonymous** volume → `/logs` (a recreated container starts with fresh logs; the old ones stay in the orphaned volume). Environment variable names (values not recorded): `NEO4J_AUTH`, plus image defaults. `NEO4J_AUTH` only applies when `/data` is first initialised, so a container re-attached to the same volume keeps the existing password.

Facts checked on 2026-11-08 that make the loopback binding safe here: the app uses `bolt://localhost:7687`; `localhost` resolves to `127.0.0.1` and `::1`, and the Neo4j driver tries each resolved address in turn, so an IPv4-only binding falls back correctly; on this Docker Desktop, a container reaches a `127.0.0.1`-published host port through `host.docker.internal` (tested with a throwaway server), so containerised tests keep working. Evaluation and tests share this one instance and are **namespaced, not isolated**: every node is keyed by its repository's root path, and cleanup is scoped to temporary paths (see `docs/evaluation.md`).

## 7. Sandbox Deployment — CRITICAL, read alongside `docs/security.md`

**Status: IMPLEMENTED AND TESTED (Phase 10, re-verified this phase) — architecturally separated from the application, not merely configured to be.**

This is the single most important production-architecture decision in this phase: **the Phase 10 sandbox image (`backend/sandbox/docker/python-test.Dockerfile`) and the Phase 15 application image (`backend/Dockerfile`) are two separate, unrelated Docker images, built from different Dockerfiles, with no shared base and no shared runtime.**

| | Application container (`backend/Dockerfile`) | Sandbox container (`backend/sandbox/docker/`) |
|---|---|---|
| Purpose | Runs THIS project's own trusted code | Executes UNTRUSTED repository/test code |
| Holds credentials | Yes (`DATABASE_URL`, `LLM_API_KEY`, `GITHUB_TOKEN`, ...) | **No — verified: zero `-e`/`--env-file` flags in every constructed `docker run` command (Phase 10/14)** |
| Has Docker CLI/socket access | **No — verified: `which docker` inside the built image returns nothing; no `docker.sock` mount anywhere in `backend/Dockerfile`** | No — it's the container BEING run, not one that runs other containers |
| Network access | Whatever the deployment platform gives the API process (needs to reach Postgres/Neo4j/LLM/GitHub) | **`--network none` by default — verified against a real container attempting a real socket connection** |
| Filesystem access | Whatever the container filesystem provides | **`--read-only` root + a copy of the target repo, never the real one — verified: a real container write is confirmed absent from the host afterward** |
| Resource limits | Container-level (deployment platform's own CPU/memory limits) | **`--memory`/`--cpus`/`--pids-limit`, verified this phase against a real fork bomb (contained, did not hang or exhaust host resources)** |
| Lifetime | Long-running (a Gunicorn/Uvicorn process serving requests) | **One-shot per test run — a fresh temp workspace copy, a fresh container name (`ai-swe-agent-sandbox-<uuid>`), removed via `--rm` or force-killed on timeout, verified via `docker ps` showing nothing left behind** |
| Image provenance | Built from `backend/Dockerfile` in this repository, from official `python:3.11-slim` | Built from `backend/sandbox/docker/python-test.Dockerfile`, also from official `python:3.11-slim`, rebuilt independently, never pulled from an untrusted registry |
| Image rebuilding | Rebuilt on every deploy from source, like any application image | Built once locally (`docker build -t ai-swe-agent-sandbox-python:latest ...`) and reused across test runs — rebuilding it is a manual, deliberate step (not automatic on every sandbox invocation), documented in `docs/architecture.md`'s "Sandbox" section |
| Temp workspace cleanup | N/A | Each run uses a fresh `tempfile.TemporaryDirectory()` for the repo copy, deleted when the `with` block exits regardless of success/failure/timeout |

**The application container must NEVER be given Docker socket access.** If a future API endpoint needs to TRIGGER a sandboxed test run (rather than a developer running scripts directly, as today), the correct pattern is: the API process shells out to `docker run` (as `sandbox/docker_runner.py` already does) using the HOST's Docker daemon that the deployment platform provides to it in a controlled way (e.g., a dedicated CI/worker node with Docker available, not the same container serving user-facing HTTP traffic) — or, more robustly for a multi-tenant production system, a dedicated worker service with its own least-privilege Docker access, decoupled entirely from the API process's database/LLM/GitHub credentials. This is a **documented target**, not implemented, since no such endpoint exists yet (see section 4).

**Update (F8): agent tasks in the API container.** The agent worker runs inside the API process, and the API image deliberately has no Docker CLI or socket. So in a containerised deployment, an approved change is **applied but its tests cannot run**. The task's response says so explicitly ("Tests: could not run (Docker is not available…)"), and the capability strip shows the sandbox as unavailable. It is never reported as verified. Running tests in production needs the dedicated sandbox-worker service described above, an architecture decision not made in F8. Mounting `/var/run/docker.sock` into the API container would give the process holding every credential root-equivalent control of the host: **do not.**

**Update (F8): untrusted code no longer runs as root.** The sandbox image has no `USER`, so before F8 repository code ran as uid 0 inside the container (verified: `os.getuid()` printed `0`). `DockerTestRunner` now passes `--user <invoking uid>:<gid>`, or `65534:65534` (`nobody`) if the app itself runs as root. This also avoids root-owned files in the bind-mounted workspace on Linux hosts. Tested against a real container: `tests/security/test_docker_sandbox_security.py::test_untrusted_code_runs_without_root_and_can_still_use_its_workspace`.

## 8. Secrets / Environment Management

**Status: IMPLEMENTED AND TESTED.**

| Variable | Category | Required |
|---|---|---|
| `DATABASE_URL` | SERVER SECRET | **Yes** |
| `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD` | SERVER SECRET | No (optional capability) |
| `LLM_API_KEY` | SERVER SECRET | No |
| `EMBEDDING_API_KEY` | SERVER SECRET | No |
| `GITHUB_TOKEN` | SERVER SECRET | No |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | SERVER SECRET / SERVER CONFIG | No |
| `APP_ENV` | PUBLIC (not sensitive) | No (defaults to development behavior) |
| `ALLOWED_ORIGINS` | PUBLIC (not sensitive — it's a list of origins, not a credential) | No |

This exact list is defined once, in `backend/config.py`, consulted by both `backend/scripts/check_production_config.py` and `backend/api/health.py`'s readiness check — they cannot disagree.

Verified this phase:
- `.env` remains local-only, gitignored (`git status` throughout this entire project's history never shows it tracked).
- `.env.example` contains only placeholders — never a real value (audited: every line is either empty or a comment).
- `check_production_config.py` never prints a value — only `configured`/`MISSING (required)`/`not set (optional)` labels (tested: `tests/test_config.py::test_check_environment_never_includes_actual_secret_values` sets an obviously-fake secret value and confirms it never appears anywhere in the returned data).
- No secret is baked into the Docker image (section 4).
- No secret is ever passed into the Phase 10 sandbox (Phase 10/14, re-verified).
- No secret appears in an observability trace or log line (Phase 13/14's `observability.redaction`, reused — not re-implemented — by `backend/api/health.py`'s own "never echo the raw exception" choice).
- Production secrets are expected to be injected by whatever platform hosts the container (its own secret manager / environment variable injection) — never a file shipped in the image.

**Update (F8): variables added since Phase 15.** `API_SERVICE_TOKEN` (server secret, ≥ 32 chars, shared with the frontend), `WORKSPACE_ROOT` (absolute, dedicated clone directory), `INGESTION_WORKER_ENABLED`, `INGESTION_MAX_REPOSITORY_KB`, `AGENT_WORKER_ENABLED`, `AGENT_APPROVAL_TTL_SECONDS` (config), and `EVALUATION_DATABASE_URL` (evaluation only, never read by the API). Frontend variables are listed in section 3. `.env.example` and `frontend/.env.example` hold placeholders only. `check_production_config.py` reports set/unset only; F8 did not change it.

## 9. Health / Readiness

**Status: IMPLEMENTED AND TESTED.**

See section 4 and `backend/api/health.py`'s own module docstring for the full liveness/readiness rationale. Summary: `/health/live` never checks external services (always `{"status": "alive"}` if the process can respond at all); `/health/ready` checks PostgreSQL (REQUIRED — gates overall readiness) and Neo4j (OPTIONAL — reported individually, never gates it), each with a 2-second connection timeout so a hung dependency can't hang the health check itself. 10 tests in `tests/api/test_health.py` + `tests/api/test_main.py`, including three real scenarios proving the Neo4j-optionality behavior specifically (unconfigured, configured-but-down, reachable) and one proving no secret value ever leaks into a dependency's status detail.

## 10. Observability in Production

**Status: IMPLEMENTED AND TESTED (Phase 13, wired into the API process this phase).**

`APP_ENV=production` calls `observability.logging_config.configure_json_logging()` at startup — every log line (including the API's own request/error logs and every `Tracer` span/event log from Phase 13) becomes one JSON object per line, ready for any log aggregator. Nothing new was built here beyond that one wiring call; Phase 13's tracer, redaction, and local `JSONFileRecorder`/`inspect_trace.py` are unchanged and still the primary way to inspect an agent run (once the API exposes agent operations — see section 4). Chain-of-thought is never exposed (Phase 13's own design); prompts, full repository files, full diffs, and credentials are never logged in full (only lengths/outcomes — Phase 13/14). The optional `LangSmithExporter` remains optional and unwired by default, exactly as documented in `docs/architecture.md`'s "Observability" section — this phase did not touch it, and does not claim a live hosted LangSmith deployment exists (none does; no `LANGSMITH_API_KEY` is configured in this environment).

## 11. CI/CD

**Status: `.github/workflows/ci.yml` — READY TO RUN, NOT YET VERIFIED ON GITHUB ACTIONS** (that needs a push; until a run is observed green, treat every claim about CI below as untested). Its YAML was parsed locally, and its pip-audit command was run locally against the built image (section 12). Nothing else in it has been observed passing on GitHub's runners.

Job `verify` (Postgres + pgvector and Neo4j service containers): installs pinned dependencies and pinned test tools (`pytest==8.4.2`, `mypy==1.19.1`, `types-requests`, `httpx`, `pip-audit==2.9.0`); creates a separate application database (`ai_swe_agent_app`, so the pytest guard below holds); builds the sandbox image; runs the full suite, `tests/security`, `mypy backend` and `git diff --check`; builds the backend image and checks non-root, `git` present and no `.env`; runs **pip-audit on the image's own `pip freeze`** against `.github/pip-audit-accepted.txt`; runs `check_production_config.py`.

Job `frontend` (F8): `npm ci`, `npm test` (Vitest), `npm run build`, `npm audit`. The workflow token is `permissions: contents: read`.

**Tested against what CI and the image actually install (F8, 2026-11-08):** nothing pins transitive dependencies, and the local 3.9 venv differs from the 3.11 image in 26 of 63 shared packages, including `langchain-core` 0.3.86 vs **1.6.10** and `ormsgpack` 1.11 vs 1.13. So the suite was also run **inside the backend image** (exact shipped packages, Python 3.11.17, source mounted read-only, host test databases). That run found two problems the local runs could not:
- three git tests depended on a global git identity (absent in the image and on Actions runners), now hermetic;
- with ormsgpack 1.13, a refused checkpoint surfaced as `internal_error` instead of `checkpoint_serialization_refused`, now mapped (`agent/checkpointing.py`; still no JSON fallback either way).

After the fixes: 1081 passed, 0 failed, 13 skipped (no sandbox inside the API image, by design), 5 deselected (real-GitHub tests, to save quota).

No deploy job: there is no provisioned target. A future `cd.yml` should be gated on this workflow and use GitHub encrypted secrets.

**Test database guard (F8):** database fixtures delete whole tables in `TEST_DATABASE_URL`, so `tests/conftest.py` refuses the pytest session (exit 2, before any test runs) when `TEST_DATABASE_URL` is the application's database. Actual behaviour:
- The application's database is `evaluation.safety.application_database_url()`: `DATABASE_URL`, or, when it is **unset**, the default the app falls back to (`<user>@localhost:5432/ai_swe_agent`). An unset `DATABASE_URL` does not disable the guard (it did before the F8 review fix).
- An identical URL is refused without connecting; otherwise both databases are compared by server identity (cluster `system_identifier` + `current_database()`), so `localhost` vs `127.0.0.1` or extra parameters do not slip through.
- Not refused: an unreachable application database (nothing to protect) or an unreachable test database (database tests skip).
- The only override is `PYTEST_DATABASE_URL_IS_VERIFIED_DISPOSABLE=1`, set by the evaluation CLI's `--with-regression` for a database it has already verified is not the application's. Nothing else should set it.
- It protects PostgreSQL only. Neo4j is shared and namespaced, not guarded (section 6).
Tests: `tests/test_f8_database_guard.py`.

**Real-GitHub tests (F8):** 5 tests clone or query `octocat/Hello-World` unauthenticated (60 requests/hour per IP; CI runners share IPs). They now **skip with the reason** when fewer than 15 requests remain, instead of failing with `403 rate limit exceeded` (`tests/github_integration/conftest.py`). Found in F8 when repeated local runs exhausted the quota. The ingestion job itself handled it correctly, ending with `github_rate_limited`. Treat a CI run where they skipped as not having exercised GitHub.

**Python version:** CI and the image use Python 3.11; this project's local `.venv` is 3.9.6. Results are close but not identical (e.g. PyPI offers newer starlette/requests only for ≥ 3.10). Recommended: move the local venv to 3.11.

## 12. Dependency / Image Security

**Status: measured in F8, 2026-11-07.**

**Python, measured on what ships:** the backend image's own `pip freeze` (64 packages, Python 3.11.17), `pip-audit 2.9.0 -s osv --no-deps`: **20 known vulnerabilities in 7 packages**. The Phase 15 figure (26 in 12) came from a 3.9 resolution and is superseded. Each is listed with its reachability reasoning in `.github/pip-audit-accepted.txt`. Summary:

| Package (version) | Advisories | Assessment |
|---|---|---|
| starlette 0.41.3 (via fastapi 0.115.6) | 7 | 2 reachable: Host/path poisoning of `request.url`, **mitigated in code** (section 4). 5 not reachable (forms, FileResponse, StaticFiles, HTTPEndpoint unused; python-multipart not installed) |
| requests 2.32.5 | 1 | not reachable (needs a local attacker; function unused) |
| langgraph / langgraph-checkpoint | 4 | 2 mitigated (JSON/pickle checkpoint encodings refused by `SafeCheckpointSerializer`); 1 not reachable (no node cache); **1 partly mitigated**: msgpack checkpoints can reconstruct objects, exploitable only with write access to the checkpoint tables |
| langgraph-checkpoint-postgres | 2 | not reachable (only `PostgresSaver`, never a Store) |
| langgraph-sdk | 2 | not reachable (never imported) |
| langsmith | 4 | not reachable (optional exporter not wired into the API; no prompt pulls, middleware or header parsing) |

None of the 20 is **fixed**: the vulnerable code still ships. 4 are **mitigated** (reachable, impact blocked by this app's code), 1 is **partly mitigated**, and 15 are **not reachable**. The accept-list labels each one.

**Trial (2026-11-08, throwaway container, nothing changed in the repo):** `fastapi==0.133.0` (the first release without Starlette's `<1.0` cap), `starlette==1.3.1` and `requests==2.33.0` on Python 3.11. `pip check` was clean; the full suite gave 1081 passed and 0 failed; OSV found no known vulnerabilities in those three. That upgrade would fix 8 of the 20.

**Upgrade path (not done in F8, deliberately):**
1. Move the local venv to Python 3.11. On 3.9, PyPI offers at most starlette 0.49.3 and requests 2.32.5, which cannot fix the reachable advisories.
2. Upgrade `fastapi` to a release allowing starlette ≥ 1.3.1, and `requests` ≥ 2.33.0. Re-run the full suite.
3. Separately, upgrade langgraph to 1.x with langgraph-checkpoint ≥ 4.1.1 and langgraph-checkpoint-postgres ≥ 3.1.3. That is a major version that touches the F6 checkpoint serializer; re-run `tests/agent_tasks` and the F7 lifecycle evaluation.

Then delete the fixed entries from the accept-list.

**npm:** `npm audit` reports **0 vulnerabilities** (Next.js 16.4.5, locked by `package-lock.json`).

**Images:** `python:3.11-slim` for both images; no image scanner (e.g. `docker scout`) was run. No transitive Python lockfile exists; the CI audit uses the image's actual `pip freeze` for that reason.

## 13. Production Config Validation

**Status: IMPLEMENTED AND TESTED.**

`backend/scripts/check_production_config.py` — see section 8. Run before deploying:

```bash
set -a; source .env; set +a
.venv/bin/python backend/scripts/check_production_config.py
```

Exit code `0` if every REQUIRED setting (`DATABASE_URL`) is configured, `1` otherwise — safe to wire into a deploy pipeline's pre-flight check.

## 14. Database Migrations / Startup

**Status: IMPLEMENTED AND TESTED.**

See section 5. `apply_schema.py` is the one explicit migration step; application startup (`backend/api/main.py`'s `lifespan`) does NOT run it, seed any data, or perform any mutating database call — confirmed by reading the `lifespan` function itself, which only configures logging and logs a line. No destructive operation exists anywhere in the startup path.

## 15. Backups / Recovery

**PostgreSQL — procedure TESTED locally (F8, 2026-11-07):**

```bash
pg_dump -Fc -d "$DATABASE_URL" -f backup-$(date +%F).dump    # read-only against the source
createdb <new_database>                                       # restore into a NEW database
pg_restore -d <new_database> --no-owner backup-YYYY-MM-DD.dump
DATABASE_URL=<new_database_url> python backend/scripts/apply_schema.py   # idempotent
```

Verified by dumping the development database (65 KB) into a scratch database: row count and content checksum of all 8 tables were identical to the source. `apply_schema.py` then left the data unchanged. The scratch database and dump file were deleted afterwards. This covers repositories, chunks, ingestion jobs, agent tasks **and the LangGraph checkpoint tables**, so pending approvals survive a restore. Note that restored `awaiting_approval` tasks keep their original expiry.

In production, prefer the managed provider's automated snapshots / point-in-time recovery. The commands above are the portable fallback and the way to test a restore.

**Neo4j — RECOMMENDATION, not tested:** Neo4j Community's `neo4j-admin database dump` needs the database stopped. The graph is **derived data**: re-indexing a repository with "build graph" rebuilds it from source, so a lost graph means downtime for graph features, not data loss.

**Cloned repositories (`WORKSPACE_ROOT`):** also derived (re-ingest from GitHub). Back them up only if local changes applied by agent tasks must be kept and have not been pushed.

## 16. Scaling Considerations

**Status: audited and documented — one real, non-obvious limitation found.**

The API itself is stateless in the ordinary HTTP sense (no in-memory session state across requests) — BUT: `agent.graph.build_agent_graph` defaults to LangGraph's `MemorySaver()` checkpointer when none is passed, and `MemorySaver` holds conversation/approval state **in the process's own memory**. If the API ever exposes agent operations (section 4) behind MULTIPLE worker processes or replicas, a `resume()` call for a given `thread_id` could land on a different worker than the one that handled the original `run()` — and `MemorySaver`'s state would not be there, silently breaking the human-approval flow. **This is a real architectural finding from this phase's audit, not a hypothetical one.** It does not affect the current minimal API (which exposes no agent operations at all), but it is the first thing that would need addressing before scaling a future agent-operations endpoint horizontally: a shared checkpointer (LangGraph supports a Postgres-backed one) or sticky session routing to the same worker, whichever fits the eventual deployment platform. No such infrastructure (e.g. Redis) is added here, per this phase's own instruction not to add infrastructure without a demonstrated need — none exists yet, since the capability that would need it doesn't exist yet either.

**Update (frontend phase F6): addressed for agent tasks.** The API's agent worker now uses LangGraph's `PostgresSaver` (same PostgreSQL, no new infrastructure), so a pending approval survives an API restart and can be resumed by any process sharing the database — verified by `tests/agent_tasks/test_checkpointing.py` (pause in one OS process, resume in a fresh one) and `test_executor.py::test_approval_survives_an_executor_restart`. Concurrency: claims use `FOR UPDATE SKIP LOCKED`; a decision is accepted by one conditional UPDATE (20 concurrent decisions → exactly one accepted, tested); `AgentService.resume` additionally serializes decisions per thread within a process. What is NOT claimed: a task whose worker dies *mid-run* is not resumed — it is marked `interrupted` once its heartbeat is stale (120 s), because re-running a half-finished apply/test step is not safe to automate. `MemorySaver` remains the default for tests, evaluation and demo scripts (`agent.checkpointing.memory_checkpointer`). Multi-worker operation was tested with multiple threads/connections against one database, not under a real multi-process Gunicorn deployment.

## 17. Rollback Strategy

**Status: DOCUMENTED TARGET.** Since `apply_schema.py` (section 5/14) is purely additive (`CREATE ... IF NOT EXISTS`, never `DROP`/`ALTER ... DROP COLUMN`), there is no destructive schema change to roll back FROM — reverting the application to a previous Docker image tag is sufficient; the schema remains compatible with an older application version as long as no column is ever removed (a discipline to maintain going forward, not something enforced by tooling today). No blue/green or canary deployment infrastructure exists or is proposed here — for a single-instance-scale deployment, "redeploy the previous image tag" is the appropriate, simple rollback mechanism, not overbuilt tooling for a scale this project hasn't reached.

## 18. Security Considerations

Fully covered in **[docs/security.md](security.md)** (Phase 14) — not duplicated here. This phase's additions layer on top of it without weakening anything: the application container has no Docker socket and no Docker CLI (verified); since frontend phase F3 it DOES include the `git` CLI, which the in-process ingestion worker needs to clone repositories (see section 4's "Update (F3)"); secrets are never baked into the image or the build context (`.dockerignore`); the health/readiness endpoints never leak a connection string or exception detail; CORS defaults to fully closed.

## 19. Known Limitations (as of F8)

- **Not deployed anywhere.** The CI workflow has **not yet been verified on GitHub Actions** (never run there).
- **Tests do not run for agent tasks in a containerised API** (no Docker in the API image by design). Changes are applied and reported as unverified (section 7).
- **20 Python advisories accepted, with reasons** (section 12). The real fix needs Python 3.11 locally plus fastapi/requests and langgraph upgrades.
- **Rollback of an applied agent change is not implemented.** An approved change is written to the clone; a failing test after approval is reported as such, never undone (`docs/evaluation.md` → "No rollback"; asserted by the `modsafety-failed-tests-no-rollback` evaluation case).
- **Single operator, no per-user authorization.** Ownership checks are "this task belongs to this repository", not "this user owns it".
- **Per-process login rate limiter**, and stateless sessions that cannot be revoked before expiry, except by rotating `SESSION_SECRET` (`frontend/lib/server/rate-limit.js`, `session.js`). No rate limit on Ask / agent submission beyond authentication and one active task per repository.
- **CSP verified at the HTTP level only** (headers and nonce placement on a real `next start`); real-browser console verification is outstanding (section 3).
- **No PostgreSQL connection pooling** (connection per operation; adequate at single-operator scale).
- **Neo4j is shared** by development, tests and evaluation, namespaced rather than isolated. It has no TLS locally, `restart=no`, and is **exposed on the LAN** (all interfaces); it needs the loopback binding before use on an untrusted network (section 6).
- **No frontend container image and no Docker Compose file.** Two images and a Next.js process are wired by hand.
- **Local Python 3.9 vs CI/image 3.11, and no transitive lock.** 26 of 63 shared packages differ, so local test runs do not test what ships. Run the suite in the image, or move to a 3.11 venv with a constraints file.

## 20. What Is Implemented and Tested

Phases 1–15 and frontend phases F1–F7 (see `docs/architecture.md`, `docs/security.md`, `docs/evaluation.md`), plus from F8: the non-root sandbox user (real-container test); the Host-independent `no-store` decision; tightened CORS headers; the nonce CSP and HSTS (unit tests plus a real `next start`); the pytest application-database guard (unit tests plus a real session refusing with exit 2); and the backup/restore procedure (section 15). Exact counts are in the F8 report (section 23).

## 21. What Is Ready to Deploy But Not Deployed

The backend image (built in F8; non-root `appuser`, Python 3.11.17), the frontend production build (`next build` / `next start`, run locally), and the CI workflow (two jobs, not run on GitHub). Each needs a real hosting target, TLS and secrets; none was provisioned.

## 22. What Is Only a Documented Target Architecture

A dedicated sandbox-worker service so deployed agent tasks can run tests (section 7); managed PostgreSQL/Neo4j hosting with automated backups; a CD pipeline; a shared login rate-limit store for multi-instance frontends; connection pooling; a frontend container image or Compose file if a container platform is chosen.

## 23. F8 Production-Readiness Review (2026-11-07)

**Assessment: READY WITH LIMITATIONS for a single-operator deployment on a private network behind HTTPS.** Not production-ready for multi-user or internet-exposed API use. Nothing was deployed, so this assessment covers the code and artifacts, not a running environment.

**Open items, stated precisely (2026-11-09):**
- **Python advisories: mitigated, not fixed.** All 20 accepted advisories still ship (4 mitigated, 1 partly mitigated, 15 not reachable; section 12). Fixing them needs the separate Python 3.11 + dependency-upgrade workstream.
- **CI: not yet verified on GitHub Actions.**
- **CSP: verified at the HTTP level; real-browser console check outstanding.**
- **Neo4j: exposed on the LAN;** loopback binding needed before use on an untrusted network.
- **Agent-task tests in a containerised API do not run** until the separate sandbox-worker workstream exists (section 7).

Fixed in F8: sandbox code ran as root; a forged Host header could strip `no-store` from source-code API responses; no script-restricting CSP and no HSTS; pytest could run its destructive fixtures against the application database; CORS allowed any request header; CI had no frontend checks and no Python dependency audit; CI test tools were unpinned; this document was stale.

Reviewed and unchanged (sound): the BFF auth chain (Argon2id, signed `__Host-` cookie with `SameSite=Strict`, Origin and session-bound CSRF checks on every mutating route, login limiter before hashing); the backend service token (constant time, fail closed, attached once at the router); bound approval (one conditional `UPDATE` on status, interrupt ID, diff hash and unexpired TTL); `SKIP LOCKED` claims; stale-heartbeat recovery reporting `interrupted`; workers surviving database outages; path-traversal and symlink defences (re-run in `tests/security`); F7 evaluation isolation.
