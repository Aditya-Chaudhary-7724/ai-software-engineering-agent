# Deployment progress log

Running log for taking the project to a public demo on free infrastructure
(Oracle Cloud A1 + docker compose + Caddy + DuckDNS + Groq). Newest entries
at the bottom of each section. A new session should read this file first,
then `docs/deployment.md`.

Status words: **done** (built and its tests pass), **verified** (observed
working on the real target), **not verified**, **todo**, **blocked (owner)**.

## Phase 0: audit (2026-11-26)

State found:
- Branch `feature/f6-agent-approval`, clean tree, 4 commits ahead of
  `origin/feature/f6-agent-approval` (demo model, demo limits, propose-only
  demo tasks, pre-generated examples). None of them has been through CI.
- No open PR could be listed: `gh` was just installed and is **not logged in**
  (owner checklist item).
- Local dev DB `ai_swe_agent` was at schema version 3; code is at 4.
  Backed up to `~/backups/ai-swe-agent/dev-pre-schema-v4-20261126T195002.dump`
  (pg_dump -Fc, 11 tables), then `apply_schema.py` → version 4.
- Docker daemon was stopped; started it and `ai-swe-agent-neo4j`.

Gates (all in `.venv311`, `.env` exported):
| Gate | Result |
|---|---|
| backend pytest | 1319 passed, 0 skipped, 0 failed (93.7 s) |
| mypy backend | no issues, 183 files |
| frontend vitest | 315 passed (16 files) |
| frontend build | OK |
| npm audit | 0 vulnerabilities |
| pip-audit (shipped image `pip freeze`, OSV) | 0 new; 20 accepted (unchanged) |

Problems found:
1. Dev DB schema out of date (fixed above).
2. `backend/Dockerfile` does not bake the fastembed model in, although the
   plan and `.env.example` say the production image does (`EMBEDDING_MODEL_PATH`).
3. No frontend demo mode (backend demo is complete): no "View demo" button,
   no demo session role, no banner, no examples UI, no `proposed` status in
   the task UI, no friendly demo limit messages.
4. Frontend server has no way to send `X-Demo-Client-IP` / the demo token.
5. Missing deploy artifacts: frontend Dockerfile, `docker-compose.prod.yml`,
   Caddyfile, CD workflow, `bootstrap.sh`, backups, `restore.sh`, demo
   seeding script.
6. Stale docs: `docs/deployment.md` sections 3/19/21/22 say "no frontend
   image / no compose / no CD"; section 4's opening paragraph says the API has
   no agent endpoints; section 11 says there is no `gh` CLI.
7. Known and unchanged: 20 accepted Python advisories (mitigated, not fixed);
   local Neo4j container published on all interfaces (dev machine only).
8. The demo-examples route answers `schema_out_of_date` for a missing table,
   where every other route uses `schema_not_initialized` (the frontend must map
   both).

## Phase 1: build (2026-11-26) — done

Demo repositories: **pallets/markupsafe, pallets/itsdangerous, psf/requests**
(`backend/demo/seed_repos.json`).

| Piece | Status | Commit |
|---|---|---|
| Demo backend slices | done (before this log) | ff7bd5d..e795ba5 |
| Fix: login limiter trusted the client-chosen X-Forwarded-For entry | done | 767e798 |
| Demo frontend (View demo, signed role, banner, examples, friendly 429s, proposed tasks) | done | 37cb0f3 |
| Fix: .dockerignore let frontend/.env.local, node_modules, .next, .venv311 into the build context | done | 69f1035 |
| fastembed model baked into the backend image (offline, read-only verified) | done | 53527b6 |
| Frontend Dockerfile (Node 24.19, standalone, non-root, 0.0.0.0) | done | c46c8ab, b20a995 |
| Demo seeding script | done | 020b2b1 |
| docker-compose.prod.yml, Caddyfile, deploy/backup/restore/duckdns/bootstrap/make-prod-env | done, rehearsed locally | 685da2c |
| CD workflow (arm64 → GHCR by SHA → SSH deploy, rollback; private images) | done, linted, not run yet | b57e47d, 8bd13a8 |

Gates at the end of Phase 1: backend 1358 passed / 0 skipped, mypy clean
(184 files), frontend 342 passed, build OK, npm audit 0, pip-audit 0 new
(20 accepted), actionlint clean, shellcheck no warnings.

Local rehearsal of the whole stack: see `docs/deployment.md` section 24.

Prepared on the operator machine (no values printed anywhere):
- `~/.ssh/ai-swe-agent-oracle` (+ .pub): the operator's SSH key for the VM.
  Fingerprint SHA256:xcIc40FdjHHj2r84XuJFAacXtQhaYIWyNIfpaej6cr4.
- `~/.ssh/ai-swe-agent-deploy` (+ .pub): a separate key for the CD workflow.
  Fingerprint SHA256:frAIepG4thUpAx+x3BsPxmPwpT0Q+C1IRUeBJQm6NoA.
- `~/.config/ai-swe-agent/prod.env` (mode 600) with the generated secrets;
  still missing the owner-supplied values (checklist below).
- Branch pushed to origin at 8bd13a8. No PR yet (`gh` not logged in).

## Phase 2: owner checklist — issued 2026-11-26, waiting for "checklist done"

The checklist is in the session reply. Owner-supplied values go ONLY into
`~/.config/ai-swe-agent/prod.env`: GROQ_API_KEY, LLM_MODEL, DEMO_LLM_MODEL
(+ a comment with the demo model's daily limits), DUCKDNS_SUBDOMAIN,
DUCKDNS_TOKEN, SITE_ADDRESS, OPERATOR_PASSWORD_HASH, optionally
OFFSITE_BACKUP_PAR_URL; the VM's public IP (not a secret) in chat or as a
comment. `deploy/make-prod-env.sh --check` must print OK.

After "checklist done", Phase 3 order: gh PR + CI → Groq smoke (both models,
read rate-limit headers) and tune DEMO_* → GitHub environment `production`
and its secrets via gh (DEPLOY_HOST, DEPLOY_USER=ubuntu, DEPLOY_SSH_KEY from
~/.ssh/ai-swe-agent-deploy, DEPLOY_KNOWN_HOSTS pinned from the VM) → add the
deploy public key to the VM → copy deploy/ + prod.env to /opt/ai-swe-agent →
`sudo ./bootstrap.sh` → merge PR → CD deploys → seed_demo.py → enable demo
→ redeploy → verify everything on the public URL.

## Phase 3: deploy and verify

Not started.
