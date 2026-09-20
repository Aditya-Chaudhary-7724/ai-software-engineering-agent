# Deployment

This document describes the ACTUAL state of this project's production readiness, as audited and built in Phase 15 — not an aspirational architecture. Every claim is labeled as one of:

- **IMPLEMENTED AND TESTED** — real code, run for real in this environment, backed by a test or a command whose output is shown/reproducible.
- **READY TO DEPLOY** — a concrete artifact exists (a Dockerfile, a script, a config) that has been built/run locally, but has not been deployed to any actual hosting platform.
- **DOCUMENTED TARGET ARCHITECTURE** — a reasoned design for something not yet built, so the gap is explicit rather than silently assumed.

No cloud deployment was performed in this phase. No deployment metrics are invented anywhere below.

## 1. Production Architecture

```
Internet
   │
   ↓
[ No frontend exists yet — see section 3 ]
   │
   ↓
Backend API  (backend/api/ — FastAPI, Phase 15)
   │
   ├──────────────→ PostgreSQL + pgvector   (Phase 3 — vector store, IMPLEMENTED AND TESTED locally)
   │
   ├──────────────→ Neo4j                    (Phase 5/6 — knowledge graph, OPTIONAL, IMPLEMENTED AND TESTED locally)
   │
   ├──────────────→ LLM provider (Anthropic)  (Phase 4/9 — OPTIONAL; StubLLMProvider without a key)
   │
   ├──────────────→ GitHub API                (Phase 11 — OPTIONAL for public read access)
   │
   └──────────────→ isolated Docker sandbox   (Phase 10 — a SEPARATE, non-networked container per test run,
                          │                     never the application container — see section 9)
                          ↓
                    test execution
```

**What is actually there today, honestly:** the backend is a Python library (Phases 1–14: ingestion, parsing, RAG, graph, hybrid retrieval, the stateful agent, tools, code modification, the sandbox, GitHub integration, evaluation, observability, security hardening) with, as of this phase, a MINIMAL FastAPI process (`backend/api/`) wrapping it — currently exposing only health/readiness/root endpoints, not the agent's operations over HTTP. See section 4 for exactly why, and what would need to be built next.

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

**Status: DOCUMENTED TARGET ARCHITECTURE — no frontend exists.**

Audited: `frontend/` is an empty directory (checked out from initial project scaffolding — see `docs/architecture.md`'s own "Status: PLANNED" marking, unchanged since). There is no `package.json`, no Next.js app, no build command, no client/server environment variable split to audit — nothing to containerize, build, or deploy. This document does not invent one.

**Target, when built:** a Next.js application calling the backend API described in section 4. Vercel is the natural deployment target for such an app (per this phase's own instruction to prefer the framework's actual platform over forcing a container) — Next.js on Vercel gets CDN-backed static assets, serverless API routes if needed, and zero-config HTTPS, none of which a hand-rolled Docker+Nginx setup would improve on for this project's scale. **Whichever framework is eventually chosen, the one hard rule already documented in this phase: any secret (an API key, a database credential) must never be exposed through a client-bundled environment variable** (`NEXT_PUBLIC_*` in Next.js, or the equivalent in any other framework) — only genuinely public configuration (e.g., the backend API's public URL) belongs there.

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
- **Worker configuration**: Gunicorn + `uvicorn.workers.UvicornWorker`, the standard documented FastAPI production pairing — `--workers 2` by default in `backend/Dockerfile`, overridable via Gunicorn's own `WEB_CONCURRENCY` environment variable at deploy time.
- **Startup checks**: the `lifespan` context manager configures logging and logs a startup line; it does NOT run schema migrations or any mutating database operation on boot (see section 15 — migrations are a separate, explicit step).

**Containerization** (`backend/Dockerfile`) — built and run locally in this session:
- `python:3.11-slim` base (minimal, official, actively maintained).
- Deterministic install: pinned `backend/requirements.txt`, `--no-cache-dir`.
- Runs as a non-root user (`appuser`, uid 1000) — **verified**: `docker exec ... whoami` → `appuser`; `id` → `uid=1000(appuser) gid=1000(appuser)`.
- No secrets baked in — **verified**: `docker run --rm ... find /app -iname '*.env*'` returns nothing; `docker history --no-trunc` shows no credential value in any layer; the image's own `Config.Env` contains only build-time Python flags, nothing runtime-secret-shaped.
- `HEALTHCHECK` against `/health/live` (liveness only, matching the liveness/readiness split — an orchestrator's own readiness probe should hit `/health/ready` separately with its own retry policy).
- `CMD` runs Gunicorn with the Uvicorn worker class, no `--reload`.
- `.dockerignore` (project root) excludes `.env`/`.git`/`.venv`/`tests`/`docs` from the build context entirely — defense in depth beyond the Dockerfile's own `COPY` instructions never referencing them.

**Verified locally, this session** (see section 21 for full output):
```
docker build -t ai-swe-agent-backend:latest -f backend/Dockerfile .
docker run -d -p 8200:8000 -e DATABASE_URL=... -e APP_ENV=production ai-swe-agent-backend:latest
curl http://127.0.0.1:8200/health/live   → {"status": "alive"}
curl http://127.0.0.1:8200/health/ready  → {"ready": true, "dependencies": [...]}  (real Postgres reached via host.docker.internal)
```

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

## 9. Health / Readiness

**Status: IMPLEMENTED AND TESTED.**

See section 4 and `backend/api/health.py`'s own module docstring for the full liveness/readiness rationale. Summary: `/health/live` never checks external services (always `{"status": "alive"}` if the process can respond at all); `/health/ready` checks PostgreSQL (REQUIRED — gates overall readiness) and Neo4j (OPTIONAL — reported individually, never gates it), each with a 2-second connection timeout so a hung dependency can't hang the health check itself. 10 tests in `tests/api/test_health.py` + `tests/api/test_main.py`, including three real scenarios proving the Neo4j-optionality behavior specifically (unconfigured, configured-but-down, reachable) and one proving no secret value ever leaks into a dependency's status detail.

## 10. Observability in Production

**Status: IMPLEMENTED AND TESTED (Phase 13, wired into the API process this phase).**

`APP_ENV=production` calls `observability.logging_config.configure_json_logging()` at startup — every log line (including the API's own request/error logs and every `Tracer` span/event log from Phase 13) becomes one JSON object per line, ready for any log aggregator. Nothing new was built here beyond that one wiring call; Phase 13's tracer, redaction, and local `JSONFileRecorder`/`inspect_trace.py` are unchanged and still the primary way to inspect an agent run (once the API exposes agent operations — see section 4). Chain-of-thought is never exposed (Phase 13's own design); prompts, full repository files, full diffs, and credentials are never logged in full (only lengths/outcomes — Phase 13/14). The optional `LangSmithExporter` remains optional and unwired by default, exactly as documented in `docs/architecture.md`'s "Observability" section — this phase did not touch it, and does not claim a live hosted LangSmith deployment exists (none does; no `LANGSMITH_API_KEY` is configured in this environment).

## 11. CI/CD

**Status: `.github/workflows/ci.yml` created this phase — READY TO RUN, but NOT executed on GitHub Actions in this session** (that would require pushing, out of scope for this phase per its own instructions).

The workflow: checks out the repo, starts real Postgres (`pgvector/pgvector:pg16`) and Neo4j (`neo4j:5-community`) service containers, installs `backend/requirements.txt` plus test-only tools (`pytest`, `mypy`, `types-requests`, `httpx`), builds the Phase 10 sandbox image, runs the FULL test suite, runs `tests/security` explicitly, runs `mypy backend`, runs `git diff --check`, builds the production backend image, and verifies it runs as non-root with no baked-in `.env`. **Deliberately no deploy/CD job** — there is no actual deployment target, registry, or hosting credential provisioned for this project. If one is added later, it belongs in a separate `cd.yml`, gated on this workflow passing, using GitHub Actions' own encrypted secrets — never a value committed to source.

Every command in the workflow was reasoned through against, and mirrors, the exact commands verified locally in this session (section 21) — but the workflow file itself has not been observed to pass on GitHub's actual runners, since doing so requires a push. This is stated plainly rather than claimed as verified.

## 12. Dependency / Image Security

**Status: evaluated for real this phase — `pip-audit` was actually installed and run, not assumed clean.**

```
$ .venv/bin/pip-audit -r backend/requirements.txt
Found 26 known vulnerabilities in 12 packages
```

All 26 findings are in **transitive** dependencies (`langgraph`, `langsmith`, `langgraph-checkpoint`, `langgraph-sdk`, `langchain-core`, `starlette`, `anyio`, `urllib3`, `click`, `orjson`, `python-dotenv`) except one DIRECT dependency, `requests==2.32.5` (PYSEC-2026-2275) — and `pip index versions requests` confirms **2.32.5 is currently the latest version published on PyPI**; the advisory's suggested fix version (2.33.0) does not exist yet, so no upgrade is currently possible for it.

**Deliberate decision: no dependency was upgraded in this phase**, per its own explicit instruction not to blindly upgrade dependencies or introduce breaking changes this late. Fixing the transitive findings would mean bumping `langgraph`/`fastapi`'s own transitive pins (real, potentially breaking version jumps, e.g. `langgraph` 0.6.11 → a `1.x` line) without the time to re-verify every Phase 7–10 test against that jump in this same session. This is tracked here as an honest, concrete follow-up rather than silently ignored or falsely claimed as "scanned and clean":

```bash
.venv/bin/pip install pip-audit
.venv/bin/pip-audit -r backend/requirements.txt   # re-run to see current status
```

**Docker base image**: `python:3.11-slim`, the official, minimal, actively-maintained image — used for BOTH the application image and the sandbox image (Phase 10's own choice, unchanged). No Docker Hub authentication was configured in this environment, so `docker scout` (Docker Desktop's built-in image scanner) was not used — this is stated rather than a scan result fabricated.

**npm dependencies**: none exist (no `package.json` anywhere in this repository — confirmed by search) — nothing to audit here honestly, since there is no frontend (section 3).

**Lockfiles**: `backend/requirements.txt` pins exact versions for every DIRECT dependency (this project's existing, pre-Phase-15 convention) — there is no separate lockfile mechanism (e.g. `pip-compile`/`poetry.lock`) capturing transitive pins exactly, which is why the 26 findings above are all in versions resolved at install time rather than a committed, reproducible lock. Introducing one is a reasonable future improvement, not implemented here to avoid yet another moving part in an already-large final phase.

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

**Status: DOCUMENTED TARGET — not implemented, since there is no production database to back up.** See sections 5 and 6 for PostgreSQL/Neo4j specifics. General principle for both: prefer a managed provider's built-in automated backup/point-in-time-recovery over building bespoke backup tooling for this project's small, standard schema — there is no genuine architectural need for a custom backup system.

## 16. Scaling Considerations

**Status: audited and documented — one real, non-obvious limitation found.**

The API itself is stateless in the ordinary HTTP sense (no in-memory session state across requests) — BUT: `agent.graph.build_agent_graph` defaults to LangGraph's `MemorySaver()` checkpointer when none is passed, and `MemorySaver` holds conversation/approval state **in the process's own memory**. If the API ever exposes agent operations (section 4) behind MULTIPLE worker processes or replicas, a `resume()` call for a given `thread_id` could land on a different worker than the one that handled the original `run()` — and `MemorySaver`'s state would not be there, silently breaking the human-approval flow. **This is a real architectural finding from this phase's audit, not a hypothetical one.** It does not affect the current minimal API (which exposes no agent operations at all), but it is the first thing that would need addressing before scaling a future agent-operations endpoint horizontally: a shared checkpointer (LangGraph supports a Postgres-backed one) or sticky session routing to the same worker, whichever fits the eventual deployment platform. No such infrastructure (e.g. Redis) is added here, per this phase's own instruction not to add infrastructure without a demonstrated need — none exists yet, since the capability that would need it doesn't exist yet either.

## 17. Rollback Strategy

**Status: DOCUMENTED TARGET.** Since `apply_schema.py` (section 5/14) is purely additive (`CREATE ... IF NOT EXISTS`, never `DROP`/`ALTER ... DROP COLUMN`), there is no destructive schema change to roll back FROM — reverting the application to a previous Docker image tag is sufficient; the schema remains compatible with an older application version as long as no column is ever removed (a discipline to maintain going forward, not something enforced by tooling today). No blue/green or canary deployment infrastructure exists or is proposed here — for a single-instance-scale deployment, "redeploy the previous image tag" is the appropriate, simple rollback mechanism, not overbuilt tooling for a scale this project hasn't reached.

## 18. Security Considerations

Fully covered in **[docs/security.md](security.md)** (Phase 14) — not duplicated here. This phase's additions layer on top of it without weakening anything: the application container has no Docker socket, no Docker CLI, and no `git` CLI (verified — it doesn't need them for its current minimal surface); secrets are never baked into the image or the build context (`.dockerignore`); the health/readiness endpoints never leak a connection string or exception detail; CORS defaults to fully closed.

## 19. Known Limitations

- No frontend exists (section 3).
- The API does not yet expose agent/evaluation/GitHub operations over HTTP — only health/readiness/root (section 4).
- No connection pooling for PostgreSQL (section 5).
- Neo4j has no production-grade persistence/TLS/backup configured anywhere (local dev only — section 6).
- 26 dependency vulnerabilities exist in transitive packages, evaluated but not fixed this phase (section 12).
- No lockfile capturing exact transitive dependency versions (section 12).
- `MemorySaver`'s in-process state would need addressing before horizontally scaling a future agent-operations endpoint (section 16).
- This CI workflow has not actually been run on GitHub Actions (section 11).
- No real cloud deployment has been performed — nothing in this document should be read as a claim otherwise.

## 20. What Is Implemented and Tested

Everything in Phases 1–14 (see `docs/architecture.md`/`docs/security.md`), plus, from this phase: `backend/config.py`, `backend/api/` (health/readiness/root + production error handling + CORS + doc-hiding), `backend/scripts/check_production_config.py`, `backend/scripts/apply_schema.py`, `backend/Dockerfile` (built and run locally, verified non-root/no-secrets/health-checked), `.dockerignore`. 41 new tests (`tests/api/`, `tests/test_config.py`), all passing locally alongside the full existing suite.

## 21. What Is Ready to Deploy But Not Deployed

The backend Docker image (built and run locally against real Postgres/Neo4j via `host.docker.internal`, verified healthy). The CI workflow (reasoned through carefully, not executed on GitHub's infrastructure). Both need an actual hosting/registry target to go further — none was provisioned or requested in this phase.

## 22. What Is Only a Documented Target Architecture

Frontend deployment (section 3), Neo4j production hosting (section 6), the "don't expose the Docker socket to a future agent-triggering endpoint" pattern (section 7), connection pooling (section 5), backups/recovery (section 15), a CD/deploy pipeline (section 11), and horizontal-scaling support for agent operations (section 16). Each is reasoned through above precisely so the gap between "designed" and "built" stays visible, per this project's own "never claim something is implemented if it is only designed" rule (CLAUDE.md).
