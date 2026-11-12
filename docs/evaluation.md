# Evaluation (Phase 12 + frontend phase F7)

Two kinds of results, never added together:

- **Deterministic**: fixed inputs, real pipeline code, and explicitly
  non-production stand-ins (`StubLLMProvider`, hash embeddings,
  `ScriptedTestRunner`). They prove **pipeline correctness and safety
  properties**, and give the same result on every run. They say nothing
  about answer quality.
- **Real-model**: needs a real LLM/embedding provider. Opt-in. Skipped with
  an explicit reason when not requested or not configured. A score is
  never produced from the stub.

## Commands

```bash
set -a; source .env; set +a                     # from the repository root

# 1. Regression tests (the source of truth for correctness)
.venv311/bin/python -m pytest -q                    # backend
(cd frontend && npm test && npm run build)       # frontend
.venv311/bin/python -m mypy backend

# 2. Evaluation report — full deterministic run (recommended)
.venv311/bin/python backend/scripts/run_evaluation.py \
    --isolated-database-url "postgresql://$USER@localhost:5432/ai_swe_agent_test" \
    --with-regression --json /tmp/eval.json

# 3. Real-model evaluation (only with real keys; costs API credits)
.venv311/bin/python backend/scripts/run_evaluation.py --real-llm --real-embeddings
```

The exit code is non-zero if any evaluated case failed. Skipped cases do
not fail the run, and are listed with their reason.

## Categories

| Category | Kind | Needs | What it checks |
|---|---|---|---|
| `retrieval` | deterministic* | Postgres (+Neo4j) | Recall/precision/MRR/hit rate for vector, keyword, hybrid, graph-hybrid |
| `rag` | deterministic | Postgres | Evidence recall, context coverage, citations grounded in context |
| `answers` | deterministic* | Postgres (+Neo4j) | The service behind Ask: each citation equals the indexed chunk **and** those lines on disk; expected file cited with keyword evidence; unanswerable questions claim no keyword evidence |
| `answers_real_model` | real-model | `--real-llm` + `LLM_API_KEY` | Answer mentions the expected identifier; unanswerable questions are explicitly acknowledged |
| `agent` | deterministic | Postgres, Neo4j | Routing, no unnecessary capability, bounded retries |
| `modification` | deterministic | Postgres, Neo4j (+Docker) | Approval before write, single-file scope, stale-change refusal, honest test-failure reporting |
| `modification_safety` | deterministic | Postgres | Protected targets (`.env`, keys, `.git/`, `node_modules/`), content must match the approved diff, deleted/binary targets, **symlinks** (into `.git/hooks` or `.env`, or out of the repository: refused; an in-repository alias: still writable), empty diff, cross-repository confinement, **no automatic rollback** (reported honestly) |
| `testing_loop` | deterministic | Postgres, Neo4j | Fix-loop bounds, per-attempt approval |
| `agent_lifecycle` | deterministic | `--isolated-database-url` | F6 task lifecycle: approve, reject (and no approval after a rejection), expiry, stale/reused/mismatched decisions, 10 concurrent decisions → 1 (then processed exactly once), 10 concurrent submissions → 1 task, worker interruption after a recorded approval, restart |
| `regression` | deterministic | `--with-regression` | Re-runs the existing approval, security, API-boundary, modification and sandbox pytest suites; exact passed/failed/skipped counts from JUnit XML |

\* With `--real-embeddings` (and `EMBEDDING_API_KEY`) these results are
labelled `real_model`.

## Isolation: what an evaluation run may touch

**Why `agent_lifecycle` needs its own database:** the agent worker claims
the oldest queued task in the table. Run against the API's `DATABASE_URL`,
a running API worker could claim an evaluation task, or the evaluation
could claim a real one.

**Why `--with-regression` is guarded too:** it re-runs pytest suites whose
fixtures `DELETE FROM agent_tasks` (and other tables) in pytest's database.
Pointed at the application database, that would wipe real data. The suites
run against ONE verified target: the isolated database when one is given
(so every evaluation write goes there), else `TEST_DATABASE_URL`. The
pytest subprocess gets **both** `TEST_DATABASE_URL` and `DATABASE_URL` set
to that target: API routes read `DATABASE_URL` per request, so a test that
forgot to monkeypatch it would otherwise talk to the developer's real
database exported from `.env`. (Checked: no current test does; the
override makes a future one harmless.) If the target is **unreachable**,
isolation cannot be verified, so the suites are refused (CLI exit 2;
cases skipped with `REFUSED: … not reachable`) rather than run.

Both are refused when the database is the **same database** as
`DATABASE_URL` — decided by what the server reports (the cluster's
`system_identifier` plus `current_database()`, `evaluation/safety.py`), not
by comparing URL text, so `localhost` vs `127.0.0.1`, keyword-form
connection strings or extra parameters cannot slip through. The CLI exits
with code 2 before any evaluation work; the runners refuse on their own as
well (cases reported as skipped, reason starting `REFUSED`). If
`DATABASE_URL` is set but unreachable, it cannot be told apart, so the run
is refused (fail closed). In CI, `DATABASE_URL` and `TEST_DATABASE_URL` are
deliberately the same disposable database; pytest itself is unaffected,
only these evaluation options refuse.

Inside the isolated database the lifecycle runner refuses to start if any
task is already active, and checks every **case** before and after: a case
starts only with no active task in the table, and a task it leaves active
fails the case (`case_left_no_active_task`) and is terminated. Each task is
processed by the case that created it.

Every repository an evaluation modifies is a fresh temporary directory
(`require_disposable_root`); placeholder output is never approved into a
real checkout or anything under `WORKSPACE_ROOT`.

**Where every other category writes.** All categories that create rows
(`retrieval`, `rag`, `answers`, `agent`, `modification`,
`modification_safety`, `testing_loop`; plus `agent_lifecycle`) index
temporary sample repositories and delete them afterwards:

| Configuration | PostgreSQL writes go to | If it cannot be used |
|---|---|---|
| `--isolated-database-url` / `EVALUATION_DATABASE_URL` set | **the isolated database only** (schema applied on first use) | unreachable → CLI exits 2; the suite reports every database category skipped ("refusing to fall back to DATABASE_URL"). Same database as `DATABASE_URL` → exit 2 / `REFUSED`. Never a fallback. |
| not set | `DATABASE_URL` (Phase 12 behaviour, kept for compatibility); the report says so in `evaluation_writes` | PostgreSQL unreachable → categories skipped |

Use an isolated database whenever possible.

**Neo4j is namespaced, not isolated.** There is one Neo4j instance (and
Neo4j Community has a single user database), so graph data cannot go to a
separate database. Instead every node the graph builder creates is keyed
by its repository's root path (a test pins this), each evaluation
repository is a unique `eval-` temporary directory, and cleanup deletes
exactly that namespace. Nodes of real repositories are never matched.

**Cleanup is guarded** (`backend/evaluation/cleanup.py`, the only place
evaluation code deletes repository rows or graph nodes): before deleting,
it reads the repository's root path back from the database and refuses
unless it is an `eval-` directory under the system temporary directory. A
bug that passes a real repository's id or path raises instead of deleting.
Nothing truncates a table or deletes by anything broader than one
namespace. The agent-lifecycle runner additionally deletes the task and
checkpoint rows it created, by id, in the isolated database only.

**Failure and interruption — what cleanup can and cannot do:**

- A case that raises, or Ctrl-C (`KeyboardInterrupt`, which `except
  Exception` does not catch): cleanup still runs (`finally`), nothing is left.
- SIGTERM (`kill <pid>`, a CI job cancelled): Python's default action ends
  the process **without** running `finally`. The CLI therefore installs
  `exit_on_sigterm` (`evaluation/cleanup.py`), which turns SIGTERM into
  `SystemExit` so cleanup runs. Code that calls the runners directly
  without the CLI keeps the default behaviour (same as SIGKILL below).
- A process that is **killed** (SIGKILL, power loss): nothing can run. Its
  rows/nodes (and temporary directory) stay behind under an `eval-`
  namespace. Every report prints `leftover_evaluation_repositories` /
  `leftover_evaluation_graphs`; a finished run has already removed its
  own data, so a non-zero count means a killed run (or another evaluation
  running at the same moment). Remove them, when no evaluation is running,
  with `run_evaluation.py --remove-leftovers` (same guard; touches nothing
  outside `eval-` namespaces).

**Also written, outside the databases:** the `agent`, `modification` and
`testing_loop` categories record real traces (Phase 13) as JSON files in
the default trace directory, which the trace viewer reads. That is
intentional (evaluation runs are traced) and unchanged; it is not
database data.

Tests: `tests/evaluation/test_f7_db_isolation.py` runs the real suite with
a stand-in application database (`ai_swe_agent_eval_guard`, created on the
test server and seeded with sentinel rows) and checks a checksum of every
table before and after; it also covers the unreachable/refused cases, the
cleanup guard, a raising case, Ctrl-C (also inside a lifecycle case),
SIGTERM with and without the CLI handler, and a SIGKILLed run. For
`--with-regression` it runs a real destructive group, and a canary test that
writes through the ambient `DATABASE_URL`, with the stand-in application
database as that ambient value: it stays unchanged and the canary row lands
in the isolated database.

## Reading the numbers honestly

- **Insufficient evidence is not detected by the system itself.** With hash
  embeddings, vector search always returns its top-k, so an unanswerable
  question still gets five (irrelevant) context chunks. The service's own
  signal (zero chunks — what Ask's `no_relevant_context` warning reports)
  stays at 0 for these cases. That's reported as an informational metric.
  Only a real model reading the context can acknowledge the gap, which is
  what `answers_real_model` measures.
- **Keyword evidence ≠ relevance.** "What does generate_jwt return?" also
  matches `greet.py` on the word *return*. `citation_precision` is reported
  (informational) so this stays visible.
- **No rollback.** A change whose tests fail stays applied. The agent offers
  a fix attempt (which needs its own approval) and says the change
  "remains applied". `modsafety-failed-tests-no-rollback` asserts exactly
  this behaviour. It is a documented limitation, not a passing quality score.
- **Real-model results so far:** none. This environment has no
  `LLM_API_KEY`/`EMBEDDING_API_KEY`; every real-model case is skipped. The
  real-model code path is unit-tested with a scripted provider only.

## Defects found while completing F7 (all fixed, each with a regression test)

| Defect | Effect | Regression test |
|---|---|---|
| `lifecycle-concurrent-decisions` left its task queued | the next case's worker claimed that leftover task; `lifecycle-worker-interruption` passed **without its approval ever being recorded** | `tests/evaluation/test_f7_isolation.py` (`…processes_only_its_own_task`, `…leaves_a_task_active…`, `…no_approval_was_recorded`) |
| "Same database as `DATABASE_URL`?" was a URL string comparison | `127.0.0.1` instead of `localhost` (or any re-spelling) let the lifecycle evaluation run on the application database | `…spelled_differently…`, `…another_spelling`, CLI exit-code tests |
| `--with-regression` had no database check | with `TEST_DATABASE_URL` pointing at the application database, the suites' fixtures would delete its `agent_tasks` rows | `…pytest_would_use_the_application_database`, CLI exit-code tests |
| Regression groups silently dropped missing test paths | a deleted suite still "passed"; with every path missing, pytest would have run the whole suite under the group's name | `…missing_path_fails_without_running_pytest` |
| Unreachable isolated database | the whole evaluation crashed instead of reporting the category as skipped | `…unreachable_isolated_database…` |
| Regression subprocess inherited the ambient `DATABASE_URL` | latent: a regression test reading `DATABASE_URL` without monkeypatching it would have written to the development database (none currently does, verified by running the groups against a stand-in) | `…pins_every_database_variable…`, `…forgets_to_override_database_url…` |
| Regression ran with an unreachable `TEST_DATABASE_URL`; ignored the isolated database | isolation was not verified before destructive suites started; with `EVALUATION_DATABASE_URL` set, regression rows still went to `TEST_DATABASE_URL` | `…regression_refuses_when_its_database_is_unreachable`, `…unreachable_isolated_database_instead…`, `…runs_every_group_there` |
| SIGTERM skipped cleanup | `kill <pid>` left rows behind like SIGKILL | `…sigterm_cleans_up_only_with_the_cli_handler` |
| No evaluation case for symlinks; no `apply_change` test for a symlink leaving the repository | write-boundary symlink protection was covered by pytest only | `modsafety-symlink-targets`; `tests/modification/test_write_boundary.py::test_apply_change_refuses_a_symlink_out_of_the_repository` |

Each new test was checked against the defect: re-introducing it in a
scratch copy makes the tests fail.

## Last recorded run (this environment, 2026-11-07)

Stub LLM, hash embeddings, Postgres + Neo4j + Docker reachable, sandbox
image present, isolated database `ai_swe_agent_test`, `--with-regression`:
46 cases — **deterministic 41 passed, 0 failed, 0 skipped; real-model 0
passed, 0 failed, 5 skipped** (reason: real LLM not requested; no
`LLM_API_KEY` in this environment). Regression groups: approval 126,
security 141, API boundaries 70, modification + sandbox 52 tests passed,
0 failed, 0 skipped — run against the isolated database (the pytest
subprocess had `TEST_DATABASE_URL` and `DATABASE_URL` both pinned to it).
Same counts as 2026-11-06. (Earlier runs: 45 cases on 2026-11-03; the new
case is `modsafety-symlink-targets`, and modification + sandbox rose from
50 to 52 with the symlink-escape tests.)

Database isolation in that run: `evaluation_writes: isolated database`,
`leftover_evaluation_repositories: 0`, `leftover_evaluation_graphs: 0`;
a row count and content checksum of every table in the development
database (`DATABASE_URL`) were identical before and after.
