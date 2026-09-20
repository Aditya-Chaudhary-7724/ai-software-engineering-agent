# AI Software Engineering Agent

## Overview

This project is an AI-powered software engineering system designed to understand, analyze, retrieve information from, and — eventually, under strict human approval — safely modify software repositories.

It is not a general-purpose chatbot with code knowledge bolted on. The goal is a system that reasons about a repository the way an engineer does: by understanding structure, dependencies, and call relationships, not just by matching text.

## Problem

Ordinary coding chatbots have real limitations when applied to real repositories:

- They lack repository-wide understanding and reason mostly about the snippet they're shown.
- Naive retrieval (e.g. plain text or embedding search) can surface irrelevant or out-of-context code.
- They do not reliably understand dependency relationships between files, modules, or packages.
- They cannot reliably reason about callers and callees, so refactors and fixes miss ripple effects.
- Software engineering tasks need structured tools (search, static analysis, test execution) rather than free-form generation alone.
- Autonomous code modification without safeguards introduces real security and correctness risks.

## Vision

The eventual system follows this flow:

```
Repository
  -> Code Understanding
  -> Code RAG
  -> Knowledge Graph
  -> Hybrid Retrieval
  -> Stateful Agent
  -> Tools
  -> Safe Modification
  -> Testing
  -> Human Approval
```

## Planned Capabilities

All capabilities below are **planned**, not implemented. See [Status](#status).

- Ingest GitHub repositories
- Understand repository structure
- Parse source code
- Extract functions, classes, and modules
- Create semantic representations of code
- Perform code-aware Retrieval-Augmented Generation (RAG)
- Build a code knowledge graph
- Perform hybrid retrieval (vector + graph)
- Run a stateful LangGraph agent workflow
- Use repository analysis tools
- Propose code modifications
- Generate patches and diffs
- Require human approval before consequential actions
- Run tests safely in a sandbox
- Analyze test failures
- Iteratively fix code based on test results
- Integrate with GitHub
- Evaluate agent, RAG, and code-generation quality
- Provide observability into agent behavior
- Enforce security boundaries throughout

## Architecture

High-level, planned architecture:

```
                  User
                    |
                    v
                Frontend
                    |
                    v
              Backend API
                    |
                    v
             AI / Agent Layer
                    |
      +-------------+-------------+-------------+
      |             |             |             |
      v             v             v             v
  Code RAG   Knowledge Graph  Repository     LLM
                                Tools
      |             |             |             |
      +-------------+-------------+-------------+
                    |
                    v
        Repository / Database / Sandbox
```

No component in this diagram is implemented yet. This represents the target architecture the project is being built toward, incrementally.

## Development Roadmap

Planned phases, to be implemented one at a time with explicit approval between each:

1. Repository ingestion — **implemented (local paths only)**
2. Code parsing — **implemented** (Python, JavaScript, JSX, TypeScript, TSX, Java, Go, C, C++)
3. Vector search — **implemented** (PostgreSQL + pgvector; real embeddings require an API key)
4. Code RAG — **implemented** (retrieval + ranking real and tested; real LLM answers require an API key)
5. Neo4j knowledge graph — **implemented** (structural relationships only; no hallucinated CALLS/USES edges)
6. Hybrid retrieval — **implemented** (vector + keyword + graph evidence merged with traceable provenance)
7. LangGraph agent — **implemented** (answer, modify, and test branches all real, behind a real human-approval gate)
8. Repository tools — **implemented** (7 of 9 fully functional; get_callers/get_callees honestly report unavailability rather than guessing)
9. Code modification — **implemented** (real diff/apply/stale-check mechanism; content quality requires an LLM API key)
10. Sandbox + testing loop — **implemented** (real Docker sandbox; bounded, human-approved fix loop after an applied change)
11. GitHub integration — **implemented** (real clone/metadata/pipeline integration; push and PR creation gated behind explicit authorization/approval, never exercised against a repository this project doesn't own)
12. Evaluation — **implemented** (real, reproducible metrics against real services; no invented numbers — see the Phase 12 section below)
13. Observability
14. Security
15. Production deployment

## Engineering Principles

- Correctness over buzzwords
- Security first
- Minimal unnecessary infrastructure
- Explainable architecture
- Testability
- Measurable evaluation
- Human approval required for consequential actions
- Repository content is treated as untrusted data, never as instructions

## Status

Project setup completed. **Phases 1-12 (repository ingestion, code parsing, vector search, code RAG, the knowledge graph, hybrid retrieval, the stateful agent, repository tools, code modification, the Docker sandbox + testing loop, GitHub integration, and the evaluation framework) are implemented.**

### Phase 1 — Repository Ingestion (implemented)

The `backend/ingestion` package can ingest a **local repository path** and produce a structured result describing what it found. Currently supported:

- Recursive file discovery, with ignored directories (`.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.next`, `dist`, `build`, `coverage`, `.cache`, `.pytest_cache`, `.mypy_cache`, `.idea`, `.vscode`) pruned during traversal rather than filtered afterward.
- Symlinks are never followed and never reported as discovered files.
- A configurable filter policy that excludes generated-artifact patterns (e.g. `*.pyc`, `*.min.js`), known binary extensions (images, archives, fonts, media, databases), oversized files (default 1 MB limit, configurable), and files whose content sniffs as binary — without blindly excluding every unrecognized extension.
- Deterministic, extension-based language detection for Python, JavaScript, JSX, TypeScript, TSX, Java, C, C++, Go, HTML, CSS, SQL, JSON, and YAML.
- Structured, dependency-free metadata models (`FileMetadata`, `RepositoryMetadata`, `IgnoredFile`, `IngestionResult`) that later phases can extend.
- Non-fatal, safe error handling for inaccessible files/directories — a single unreadable subdirectory does not abort the run.

Not yet supported (explicitly out of scope for Phase 1):

- GitHub repository cloning/ingestion (planned for Phase 11) — only local paths are accepted today.
- Reading or parsing file *contents* beyond the minimal binary-detection sniff.
- Any embeddings, vector storage, graph storage, retrieval, LLM calls, or code modification.

**Running it:**

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
.venv/bin/python -m pytest                              # run the test suite
.venv/bin/python backend/scripts/manual_ingestion_demo.py  # manual demonstration
```

### Phase 2 — Code Parsing (implemented)

The `backend/parsing` package takes a Phase 1 `IngestionResult` and extracts structured symbols from every file it can parse:

- **Python** — via the standard library `ast` module (no dependency needed; it's the same parser CPython uses).
- **JavaScript, JSX, TypeScript, TSX, Java, Go, C, C++** — via [Tree-sitter](https://tree-sitter.github.io/tree-sitter/), since no usable parser exists in the Python standard library for these and the project's multi-language ambition justifies a real grammar-based parser over regex scraping.
- **SQL** is ingested (Phase 1) but not parsed for entities in Phase 2 — functions/classes don't map cleanly onto SQL, so it's honestly recorded as skipped rather than faked.

For each parsed file: imports, classes (with base classes where the grammar exposes them), and functions/methods (with 1-indexed line ranges, parameters, and method/parent-class linkage) are extracted into dependency-free dataclasses (`ImportEntity`, `ClassEntity`, `FunctionEntity`, `ParsedFile`, `ParsingResult`) designed to be consumed directly by later RAG/graph indexing phases.

Known, documented limitations:
- `qualified_name` for Python reflects only the immediate enclosing class, not a full nesting chain.
- C/C++ function-name extraction unwraps pointer/reference declarators and C++ `Class::method` qualified names, but does not handle function pointers, templates, or operator overloads.
- Parameters are raw source text per parameter, not decomposed into (name, type) pairs.
- Base-class extraction is not attempted for TypeScript `implements` clauses or Go embedded structs.

**Running it:**

```bash
.venv/bin/python -m pytest                                # includes Phase 2 tests
.venv/bin/python backend/scripts/manual_parsing_demo.py    # manual demonstration
```

See `docs/architecture.md` for the full design and its IMPLEMENTED/PLANNED breakdown for what remains.

### Phase 3 — Vector Search Foundation (implemented)

The `backend/vectorstore` package chunks parsed code (Phase 2 output), embeds it, and stores/searches it in **PostgreSQL + pgvector** — the architecture called for in this project's technology policy, with no additional vector infrastructure (no Pinecone/Weaviate/Elasticsearch/Redis).

- **Chunking**: one chunk per extracted class/function (using Phase 2's line ranges), with a whole-file fallback for source files with no extracted symbols (e.g. SQL).
- **Embedding provider abstraction**: `EmbeddingProvider` is a small interface with two implementations —
  - `OpenAIEmbeddingProvider` (`text-embedding-3-small`, 1536 dimensions) — **REQUIRES `EMBEDDING_API_KEY`**, a real OpenAI account, and network access. Not exercised against a live API in this build (no key is present in this environment); its request/response handling is unit-tested against a mocked client.
  - `DeterministicLocalEmbeddingProvider` — a hash-based, **non-semantic** stand-in used for local development and tests. It lets the storage/indexing/search pipeline be genuinely tested end-to-end without any credentials, but similar text does **not** produce similar vectors with it — it must never be presented as real retrieval quality.
- **Storage & search**: raw parameterized SQL via `psycopg` (no ORM — unjustified for a two-table schema), with cosine-distance similarity search (`<=>` via an HNSW index) and metadata filtering by repository, language, and chunk type.
- **Schema**: `backend/vectorstore/schema.sql`, applied idempotently (`CREATE ... IF NOT EXISTS`) rather than through a full migration framework, since the schema is still evolving.

**What's genuinely tested locally vs. what needs external credentials:**
- LOCAL TESTED: chunking, the local embedding provider, and the full index→store→similarity-search pipeline — all run against a real local PostgreSQL 18 + pgvector 0.8.6 instance in this environment's test suite (18 tests, `tests/vectorstore/`), auto-skipping if no Postgres is reachable.
- REQUIRES EXTERNAL SERVICE: `OpenAIEmbeddingProvider` actually returning real embeddings (needs `EMBEDDING_API_KEY`); its code path is unit-tested with a mocked client, not a live call.

**Local setup used in this environment** (documented so it's reproducible, not because it's required generally):
```bash
brew install pgvector
createdb ai_swe_agent          # your project database — do not reuse another project's DB
psql -d ai_swe_agent -c "CREATE EXTENSION vector;"
# then set DATABASE_URL=postgresql://<you>@localhost:5432/ai_swe_agent in .env
```

**Running it:**
```bash
.venv/bin/python -m pytest tests/vectorstore                    # skips cleanly if Postgres isn't reachable
.venv/bin/python backend/scripts/manual_vectorstore_demo.py      # manual demonstration (no API key needed)
```

### Phase 4 — Code RAG (implemented)

The `backend/rag` package answers repository-level questions:

```
question -> query embedding -> vector retrieval (Phase 3)
                             -> keyword retrieval (PostgreSQL full-text search)
         -> merge & rank -> bounded context assembly -> LLM -> grounded answer + sources
```

- **Vector + keyword retrieval**: vector search reuses Phase 3's `VectorStore.similarity_search`; keyword search uses PostgreSQL's full-text search (`to_tsquery`, OR'd terms so a natural-language question doesn't need to match every word, with a GIN index for performance).
- **Ranking**: `rag/ranking.py` merges both result sets into one list, scored as `0.7 * vector_similarity + 0.3 * keyword_score` (both documented, deliberately simple — Phase 6's hybrid retrieval is expected to refine this further by adding graph evidence).
- **Context assembly**: `rag/context.py` bounds total context size (`max_context_chars`, default 8000) rather than concatenating every candidate — this is what "do not blindly dump entire files into the LLM" means in practice, on top of Phase 2/3 already chunking at the function/class level.
- **Grounded sources**: `RAGAnswer.sources` is built directly from retrieval metadata (file path + line range + symbol), independent of what the LLM's own text says — so citations are accurate even if the model's prose isn't perfectly faithful to the context (faithfulness *measurement* is a Phase 12 concern, not solved here).
- **LLM provider abstraction** (`rag/llm/`): `AnthropicLLMProvider` (production, **REQUIRES `LLM_API_KEY`** — not exercised live in this environment, unit-tested via a mocked client) and `StubLLMProvider` (a deterministic, explicitly non-LLM placeholder used to test the full pipeline without credentials).

**What's genuinely tested locally vs. what needs external credentials:**
- LOCAL TESTED: ranking, context assembly, keyword search, and the full retrieval→ranking→context→answer pipeline (using the stub LLM) — all run against real PostgreSQL (`tests/rag/`, 21 tests).
- REQUIRES EXTERNAL SERVICE: `AnthropicLLMProvider` actually generating a real answer (needs `LLM_API_KEY`); its request/response handling is unit-tested with a mocked client, not a live call.

**Running it:**
```bash
.venv/bin/python -m pytest tests/rag                     # skips cleanly if Postgres isn't reachable
.venv/bin/python backend/scripts/manual_rag_demo.py       # manual demonstration (no API keys needed)
```

### Phase 5 — Knowledge Graph (implemented)

The `backend/graph` package builds a **Neo4j** graph of a repository's structure from Phase 1 + Phase 2 output — no other graph database, per the technology policy.

**Node types**: `Repository`, `Folder`, `File`, `Class`, `Function`, `Module`.
**Relationships created** — only ones actually derivable from what's already extracted:
- `CONTAINS` — the folder/file hierarchy, from `relative_path` structure alone.
- `DEFINES` — File→Class, File→top-level Function, Class→method Function.
- `IMPORTS` — File→Module, always, from the raw import string.
- `RESOLVES_TO` — Module→File, only when an import can be matched to a real file in the same repository. Attempted for Python (including the common `from X import Y` case — see below), JavaScript/TypeScript/JSX/TSX relative imports, and Java dotted imports. **Not attempted** for Go or C/C++ (documented reasons in `graph/resolution.py`) — those imports still get a `Module` node and `IMPORTS` edge, just no resolution.
- `INHERITS` — Class→Class, only when a base-class name matches **exactly one** class anywhere in the repository. If it's ambiguous (two classes share that name) or unresolved, no edge is created — but the raw base-class name is still kept as a `base_class_names` property, so nothing is silently lost.

**Explicitly NOT created: `CALLS`, `USES`, `DEPENDS_ON`.** Phase 2 extracts *definitions* (functions, classes, imports), not call-sites inside function bodies — there's no real data to build these from yet. Faking them from name-matching alone is exactly the "hallucinated CALLS relationships" this phase was told not to produce; they'll become possible once a future phase adds call-site extraction to parsing.

**A real gap I found and fixed while building this:** Phase 2's Python parser records `from X import Y` and `import X.Y` identically as `module="X.Y"` (there's no way to tell them apart in the extracted data). The resolver tries both interpretations — the full path as a module, and the path with the last segment dropped — so the far more common `from X import Y` style resolves correctly. Verified against a real multi-file example (`main.py` → `from services.shelter import adopt` → `services/shelter.py`).

**Why a graph, not just more vector search:** vector search answers "what looks similar to this text"; it cannot reliably answer "what does this file depend on", "what does this class inherit from", or "what would break if I renamed this" — those require exact traversal of real structural relationships, which is what `graph/queries.py` demonstrates (`get_file_dependencies`, `get_importers_of_file`, `get_class_ancestors`, `find_symbol`, `get_repository_summary`).

**What's genuinely tested locally vs. what needs external setup:** everything — Neo4j runs locally in this environment (Docker container `ai-swe-agent-neo4j`) and all 28 tests in `tests/graph/` run against it for real (verified via `-v`, none skipped), including idempotency (rebuilding a repository doesn't duplicate nodes).

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/graph               # skips cleanly if Neo4j isn't reachable
.venv/bin/python backend/scripts/manual_graph_demo.py  # manual demonstration, cleans up after itself
```

### Phase 6 — Hybrid Retrieval (implemented)

The `backend/hybrid` package extends Phase 4's retrieval with a third evidence source: the Phase 5 graph.

```
question -> vector search (Phase 3) + keyword search (Phase 4)
         -> pick top seed candidates
         -> one-hop graph expansion from those seeds (Phase 5): class methods,
            inheritance neighbors, and symbols in files the seed depends on
         -> merge all three (vector, keyword, graph) with traceable provenance
         -> bounded context -> LLM -> grounded answer
```

This directly implements the phase's own worked example: for "Where is the login route implemented?", `login_route` matches by vector/keyword, and the graph expansion — following the real `IMPORTS`/`RESOLVES_TO` edges Phase 5 built — pulls in `generate_jwt`, a function the question never mentions and `login_route` never calls directly (it calls `login_user`, which calls `generate_jwt`). Every included chunk's `found_via` field names exactly which method(s) surfaced it (e.g. `("vector", "graph:dependency_symbol")`), satisfying "do not simply concatenate arbitrary results" — nothing is included without a traceable reason.

- **Graph expansion** (`hybrid/graph_expansion.py`) runs exactly one hop out from a capped number of seed candidates — bounded by construction, not a retry limit, so there's no unbounded-traversal risk regardless of repository size.
- **Ranking** (`hybrid/ranking.py`) combines all three signals: `0.5 * vector + 0.2 * keyword + 0.3 * graph`, explicitly documented as a heuristic starting point, not a tuned/measured value (that's Phase 12's job).
- **A real architectural seam, documented rather than hidden**: Phase 3 (Postgres) and Phase 5 (Neo4j) identify a repository differently (`repository_id` vs `root_path`) since they were built independently — `HybridRAGService.answer()` requires both. Unifying them is a reasonable future refinement, not done here since it would mean reopening already-tested code for convenience alone.

**What's genuinely tested locally:** all 12 tests in `tests/hybrid/` run against both real PostgreSQL and real Neo4j together (verified via `-v`, none skipped), including a rigorous, seed-controlled proof that graph expansion correctly finds a dependency chain (`test_graph_expansion.py`).

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/hybrid                # skips cleanly if either service is unreachable
.venv/bin/python backend/scripts/manual_hybrid_demo.py  # manual demonstration, cleans up after itself
```

### Phase 7 — Stateful Agent (implemented)

The `backend/agent` package wraps Phases 1-6 into a **LangGraph** state machine — not one giant autonomous loop, per the project's own agent-design principle.

```
START -> task_analyzer -> planner -> repository_search -> graph_search
       -> code_analyzer -[no results? retry, bounded]-> back to repository_search
       -> decision -> answer .......................... -> END   (fully real: Phase 6 retrieval + LLM)
                    -> test ............................ -> END   (real: Phase 10 sandbox execution)
                    -> propose_change -> human_approval -> apply_change -[applied?]-> run_tests_after_apply -> END
                                                                                       (real: Phase 9 write + Phase 10 verify,
                                                                                        with a bounded, human-approved fix loop)
```

- **Explicit state** (`agent/state.py`): a plain `TypedDict`, deliberately not dataclasses/provider objects — LangGraph checkpoints this state (required for the approval interrupt to survive across calls), so keeping it plain data avoids serialization surprises.
- **Task classification** (`agent/classification.py`) is a deterministic, rule-based keyword classifier — not an LLM call. This keeps routing fully testable without a live `LLM_API_KEY` and keeps the decision inspectable rather than hidden inside a model call.
- **Bounded retries**: if repository+graph search find nothing, `code_analyzer` retries with a broader search up to `MAX_RETRIES=2` times before honestly reporting no context was found — provably terminating, tested directly (an empty-repository test asserts exactly 2 retry log entries, then a final answer).
- **Real human-in-the-loop approval**: any `modify`-classified request is routed through a genuine LangGraph `interrupt()` — the graph actually pauses, returns control to the caller, and only proceeds after an explicit `AgentService.resume(thread_id, approved=...)` call. This is not simulated; it's the same mechanism a production approval workflow would use.
- **Honest gaps, not fake success**: a repository with no detectable test command, or a Docker sandbox that isn't reachable, are reported exactly as that — never a fake pass and never silently skipped. See Phase 9 (Code Modification) and Phase 10 (Sandbox & Testing Loop) below for what "modify" and "test" actually do now.
- **No hidden chain-of-thought**: every node appends a short, safe summary to `execution_log` (e.g. `"Searching repository... 3 vector match(es), 2 keyword match(es)."`) — never raw model reasoning.

**What's genuinely tested locally:** all 23 tests in `tests/agent/` run against real PostgreSQL and Neo4j together, including the full approve/reject interrupt cycle, the bounded-retry termination proof, and (Phase 10) the bounded fix-loop tests in `test_testing_loop.py`.

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/agent               # skips cleanly if either service is unreachable
.venv/bin/python backend/scripts/manual_agent_demo.py  # manual demonstration of all 3 branches, cleans up after itself
```

### Phase 8 — Repository Tools (implemented)

The `backend/tools` package formalizes the ad-hoc function calls Phase 7's agent nodes made directly into a proper tool abstraction: declarative **Pydantic** input/output schemas (the one place in this backend that uses Pydantic instead of plain dataclasses — this is the first genuine external-input-validation need, and JSON Schema generation matters for future LLM tool-calling registration), validation, structured error handling, and explicit authorization boundaries.

| Tool | Status | Backed by |
|---|---|---|
| `list_files` | real | fresh filesystem scan (Phase 1) |
| `read_file` | real | path-traversal-guarded read |
| `analyze_code` | real | Phase 2 parsing, single file |
| `search_code` | real | Phase 3+4 vector+keyword retrieval |
| `search_symbol` | real | Phase 5 graph exact lookup |
| `graph_query` | real | fixed enum of pre-built Cypher queries — never raw Cypher |
| `get_dependencies` | real | Phase 5 import resolution |
| `get_callers` | **honestly unavailable** | no CALLS data exists (see below) |
| `get_callees` | **honestly unavailable** | no CALLS data exists (see below) |

**A real security boundary, not just described:** every path-taking tool goes through `tools/security.py::resolve_safe_path`, which rejects any path that would resolve outside the repository root — verified against actual traversal attempts (`../../etc/passwd`, an absolute `/etc/passwd`) in both the test suite and the manual demo, not just asserted in a docstring.

**`get_callers`/`get_callees` are the one place this phase's tool list can't be honestly implemented:** Phase 5's graph has no `CALLS` relationship, because Phase 2 doesn't extract call-sites inside function bodies — there's no real data to answer "who calls this?" from. Rather than approximate it with name-matching (which would produce plausible-looking wrong answers an agent couldn't distinguish from real ones), both tools return a structured `available=False` with a clear reason. This is the same "implement the mechanism honestly, don't fake the capability" pattern as Phase 7's `modify`/`test` stubs.

**No write or execute capability exists anywhere in this package** — by construction, not by convention. `write_file`, `create_patch`, and `run_tests` are Phase 9/10's concern, and per this project's own design, code modification requires human approval (Phase 7's real interrupt gate) before anything is ever written.

**What's genuinely tested locally:** all 30 tests in `tests/tools/` — a mix of pure unit tests (security boundary, file tools, registry error-mapping — no external service needed) and integration tests against real PostgreSQL + Neo4j together for the tools that need them.

**Running it:**
```bash
.venv/bin/python -m pytest tests/tools                # unit tests always run; DB-backed ones skip cleanly if unreachable
set -a; source .env; set +a
.venv/bin/python backend/scripts/manual_tools_demo.py  # manual demonstration of every tool, cleans up after itself
```

### Phase 9 — Code Modification (implemented)

The `backend/modification` package implements the safe modification workflow, and **Phase 7's agent graph was updated to use it for real** — this is the point where the human-approval mechanism Phase 7 built goes from gating a stub to gating an actual write.

```
instruction -> find affected file (Phase 3+4 retrieval) -> generate proposed content (LLM)
            -> unified diff (stdlib difflib) -> [shown to a human — this is the interrupt payload]
            -> HUMAN APPROVAL -> apply (only if approved) -> "Tests: not run, Phase 10 planned"
```

- **`apply_change` is the only function in this entire project that writes to a repository file.** It's never called except after a real LangGraph `interrupt()` resumes with `approved=True`, and it re-reads the target file immediately before writing — if it changed since the proposal was generated (a concurrent edit), it refuses with `StaleChangeError` rather than silently clobbering it. Verified directly: a test edits the file between propose and apply and confirms the edit survives.
- **The diff is shown before approval, not after** — a genuine correctness fix over Phase 7's original ordering (which asked for blind approval, then revealed a stub). Since nothing was committed yet when this was found, the agent's graph topology was reordered rather than left wrong: `decision -> propose_change -> human_approval -> apply_change`.
- **Reuses Phase 3+4's retrieval as-is** for `find_affected_file` — "which file is relevant to this instruction" is the same problem as "which code is relevant to this question," just consumed differently.
- **Reuses Phase 8's path-traversal guard** (`tools.security.resolve_safe_path`) — the same safety boundary, not a second implementation of it.

**What's real vs. what needs a real API key:** the mechanism — finding the right file, generating *some* content, diffing it, gating on approval, applying it exactly once, refusing stale writes — is fully real and tested. With `StubLLMProvider` (no `LLM_API_KEY`), the "proposed content" is the stub's fixed placeholder text, not valid code; this proves the pipeline, not code-generation quality. Real quality requires `AnthropicLLMProvider` and a real key — the same caveat as every other LLM-dependent phase.

**What's genuinely tested locally:** all 7 tests in `tests/modification/` run against real PostgreSQL (for file-finding), using isolated `tmp_path` fixtures — never the real project repository, per this phase's own testing requirement. The 3 updated agent tests in `tests/agent/` now assert the file is *actually modified* after approval (previously they asserted the opposite, back when this was a stub).

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/modification                    # skips cleanly if Postgres isn't reachable
.venv/bin/python backend/scripts/manual_modification_demo.py      # standalone demonstration, including the stale-change refusal
.venv/bin/python backend/scripts/manual_agent_demo.py             # see it wired into the full agent (updated for Phase 9)
```

### Phase 10 — Sandbox & Testing Loop (implemented)

The `backend/sandbox` package adds an isolated Docker-container test runner, and **Phase 7's agent graph was extended to use it**: an applied change (Phase 9) is now verified by actually running the repository's tests, with a bounded, human-approved fix loop if they fail.

```
target repository -> detect test command (pytest only, this phase) -> docker run
    --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges
    --memory <limit> --cpus <limit> --pids-limit <limit> --tmpfs /tmp
    -v <copy-of-repo>:/workspace:rw ai-swe-agent-sandbox-python:latest <command>
-> capture stdout/stderr/exit code/timeout -> pass/fail
```

**Repository code never runs on this host — verified against real containers, not just configured:**
- The repository is copied first; the COPY is mounted, never the original (`test_real_container_does_not_mutate_the_original_repository`).
- `--network none` by default (`test_real_container_has_no_network_access` proves a real connection attempt fails).
- `--read-only` root filesystem + a small `/tmp` tmpfs (`test_real_container_root_filesystem_is_read_only` proves a write outside the workspace fails).
- `--cap-drop ALL --security-opt no-new-privileges`, plus `--memory`/`--cpus`/`--pids-limit`.
- **No host environment variables or `.env` secrets are ever passed** — no `-e`/`--env-file` flag exists in the command at all.
- A hard `subprocess` timeout enforces the limit; on timeout the container is force-killed (`test_real_container_is_killed_and_removed_on_timeout` proves nothing is left running).

**Why shell out to the `docker` CLI instead of adding the `docker` Python SDK as a dependency:** consistent with this project's existing choices — raw SQL via `psycopg`, raw Cypher via the `neo4j` driver, no ORM/query-builder anywhere. Docker Desktop is already required locally (Neo4j itself runs in a container), so this adds zero new Python dependencies.

**The testing loop, wired into the agent (`agent/graph.py`, `agent/nodes.py`):**

```
apply_change -[applied?]-> run_tests_after_apply -[passed]-> END
                                                  -[no test command / sandbox unavailable]-> END (honest, not faked)
                                                  -[failed, budget remains]-> propose_change (instruction built
                                                                                from the actual failure output)
                                                                           -> human_approval (a FRESH interrupt)
                                                                           -> apply_change -> run_tests_after_apply -> ...
                                                  -[failed, budget exhausted]-> END ("Giving up after N fix attempt(s)")
```

- **Bounded two ways, either one stops the loop:** `fix_iteration < MAX_FIX_ITERATIONS` (2) AND a wall-clock `loop_deadline` (`MAX_LOOP_SECONDS` = 300s) set once and carried across iterations. Neither bound alone is enough — see `docs/architecture.md`'s "Testing Loop" section for why.
- **The human-approval gate is never bypassed on a retry** — every fix attempt re-enters `propose_change -> human_approval`, and the interrupt message says which attempt it is, so a reviewer always sees exactly what will be applied before it happens.
- **The agent can never make an unbounded number of unsupervised changes** — every single write, including every fix attempt, still requires an explicit `AgentService.resume(thread_id, approved=True)` call from a human.

**What's genuinely tested locally vs. what needs real Docker:** the sandbox itself (command construction, security flags, timeout/kill behavior) is unit-tested with mocks in `tests/sandbox/test_docker_runner.py`, plus **6 real-Docker integration tests** in `tests/sandbox/test_docker_integration.py` that build actual containers and prove each isolation property above against real infrastructure (all passing in this environment). The agent's fix-loop routing (iteration cap, time cap, approval-per-retry) is tested deterministically with a fake test runner in `tests/agent/test_testing_loop.py` (4 tests, real PostgreSQL + Neo4j). The full loop against a **real** Docker container is demonstrated end-to-end in `manual_agent_demo.py`'s scenario 4: a real applied change, two real sandboxed test failures, two human-approved fix attempts, and an honest give-up.

**A real, documented limitation, not faked:** only Python repositories using pytest are supported (detected via `pytest.ini`/`setup.cfg`/`pyproject.toml`/`tox.ini` or `test_*.py`/`*_test.py` files), and the sandbox image only has `pytest` preinstalled — a target repository's own extra dependencies are not installed, since that would require enabling network access. Both gaps are reported honestly (`"No supported test command was detected..."`), never approximated.

**Running it:**
```bash
# One-time setup: build the sandbox image (never pushed to any registry)
docker build -t ai-swe-agent-sandbox-python:latest \
    -f backend/sandbox/docker/python-test.Dockerfile backend/sandbox/docker

.venv/bin/python -m pytest tests/sandbox                      # unit tests always run; real-Docker ones skip cleanly if unreachable
set -a; source .env; set +a
.venv/bin/python -m pytest tests/agent                        # includes the Phase 10 fix-loop tests
.venv/bin/python backend/scripts/manual_sandbox_demo.py        # standalone: passing/failing/hanging tests, network + filesystem isolation
.venv/bin/python backend/scripts/manual_agent_demo.py          # scenario 4: the full fix loop against a real container
```

### Phase 11 — GitHub Integration (implemented)

The `backend/github_integration` package adds real GitHub repository access — validation, metadata, cloning, and gated write operations — behind one service, and wires cloning into the EXISTING Phase 1/2/3/5 pipeline unchanged.

```
URL -> parse_github_url (validate: https://github.com/<owner>/<repo> only)
    -> GitHubAPIClient.get_repository (real metadata)
    -> resolve_clone_destination (workspace-root-confined, reuses Phase 8's resolve_safe_path)
    -> GitOperations.clone (real `git clone` via subprocess)
    -> [caller runs the existing Phase 1/2/3/5 pipeline, unchanged — github_integration/pipeline.py]
    -> GitOperations.create_branch -> GitOperations.commit_all
    -> push_branch(authorized=True only) -> create_pull_request(approved=True only)
```

**URL validation rejects everything but `https://github.com/<owner>/<repo>`** — SSH URLs, other hosts, lookalike hosts (`github.com.evil.com`), userinfo-smuggled netlocs, and path-traversal attempts (`https://github.com/octocat/../../../etc`) are all rejected before the owner/repo segments are ever used to build a filesystem path or an API call. `resolve_clone_destination` then independently reuses Phase 8's `tools.security.resolve_safe_path` **unchanged** to confine every clone to `workspace_root/<owner>/<repo>` — tested directly with an already-malicious reference that bypasses URL validation entirely, proving this is real defense-in-depth, not one layer relying on the other.

**Authentication is `GITHUB_TOKEN` only**, read from the environment (or passed explicitly for tests) — never hardcoded, never required for read-only operations against public repositories. **The token never appears in a subprocess argv list** (visible to any process on the host via `ps`) — `git_operations.py` passes it only through the child process's `GIT_TOKEN` environment variable, read at credential time by a standard git inline-shell credential helper. Verified directly: a unit test asserts the literal token string never appears in any constructed `git` command.

**The token never reaches repository code or the Docker sandbox** — not just designed that way, verified: `tests/github_integration/test_token_isolation.py` sets `GITHUB_TOKEN` in the host environment and proves the Phase 10 sandbox's constructed `docker run` command contains neither the token nor any `-e`/`--env-file` flag at all.

**Push and pull-request creation are gated exactly like Phase 9's `apply_change`:** `push_branch` requires `authorized=True`, `create_pull_request` requires `approved=True` — both raise `UnauthorizedActionError` otherwise, and neither flag is ever defaulted or inferred. Commit authorship (`author_name`/`author_email`) is always required from the caller, never invented or hardcoded — this project must never assume an identity to attribute a commit to.

**Why `requests` + the `git` CLI, not a GitHub SDK (PyGithub/githubkit):** this package only ever needs two REST calls (read a repo, open a PR) — a full SDK would hide exactly those two calls behind a much larger dependency. `requests` was already a transitive dependency in this environment and is the de facto standard synchronous HTTP client. Local git operations shell out to the `git` CLI via `subprocess` instead, the same "official CLI over an added SDK" pattern as Phase 10's Docker sandbox — zero new dependencies for that half of the package.

**What's genuinely tested locally vs. what needs a real, owned repository:** all 54 tests in `tests/github_integration/` pass in this environment, including real network calls to `api.github.com` and a real `git clone` of `octocat/Hello-World` (GitHub's own canonical example repo), plus a real-pipeline test indexing that real clone through Phase 3 against local PostgreSQL. Push and pull-request creation are mechanism-tested with mocks and demonstrated being correctly **refused** without authorization in the manual demo — an actual push or PR is never exercised against a repository this project doesn't own or control. Real push/PR execution needs a real `GITHUB_TOKEN` and a repository the caller actually owns.

**Explicitly out of scope:** GitHub Enterprise/self-hosted hosts, SSH authentication, non-GitHub hosts, and wiring this into `agent/graph.py`'s LangGraph workflow (this phase builds the capability itself, as requested; agent-workflow integration is a natural next step, not implemented here, per this project's phase-control discipline).

**Running it:**
```bash
.venv/bin/python -m pytest tests/github_integration          # real-network/Postgres tests skip cleanly if unreachable
set -a; source .env; set +a
.venv/bin/python backend/scripts/manual_github_demo.py        # real clone + real pipeline + proof push/PR are refused
```

### Phase 12 — Evaluation (implemented)

The `backend/evaluation` package is a real, quantitative evaluation framework covering retrieval, RAG, agent behavior, code modification, and the testing loop — run through the ACTUAL Phase 1-10 code (never simulated) against a small, fixed, fully-inspectable benchmark. Every number this section reports comes from an actual run of `backend/scripts/run_evaluation.py`, not a hand-typed figure.

**The dataset** (`evaluation/dataset.py`) is deliberately small — one 4-file sample repository (~15 lines total) plus a handful of explicit cases per category — so it stays "transparent enough that an interviewer can inspect it in full," per this phase's own instruction. Every case's expected result is derived by reading the sample repository's actual content, not by running the system and recording whatever it happened to return.

**Metrics implemented** (`evaluation/metrics.py`, pure functions, unit-tested with plain lists): Recall@K, Precision@K, reciprocal rank (aggregated into MRR per method in the report), and hit rate.

**An honest, load-bearing caveat about the numbers:** retrieval runs by default with `DeterministicLocalEmbeddingProvider` — the same hash-based, non-semantic stand-in used throughout this project's test suite (no `EMBEDDING_API_KEY` assumed present). So **vector-only** recall/precision/hit-rate numbers measure nothing about real embedding quality and are reported as informational (never gating pass/fail) — this is expected, not a bug. On this intentionally tiny benchmark, vector-only recall@5 is often saturated at 1.0 purely because k=5 covers nearly the whole corpus, but vector-only precision@5 (consistently lower than keyword's in an actual run) still reveals the real ranking-quality gap. **Keyword search** (PostgreSQL full-text, no embeddings) and the **hybrid methods that include it** are what this suite actually gates pass/fail on. A real vector-retrieval number needs `OpenAIEmbeddingProvider` + a real `EMBEDDING_API_KEY` (the runner accepts an injected provider — a one-line swap for a caller who has one).

**What's evaluated per category:**
| Category | What's checked | Requires |
|---|---|---|
| Retrieval | Recall/Precision/MRR/hit-rate for vector, keyword, RAG-hybrid, graph-hybrid | PostgreSQL (+ Neo4j for graph-hybrid) |
| RAG | Expected evidence retrieved, context contains expected substrings, citations grounded in what was shown | PostgreSQL |
| Agent | Task classification, routing correctness, no unnecessary capability invoked, bounded retries, safe refusal pending approval | PostgreSQL + Neo4j |
| Modification | Approval required, correct/only-file scope, stale-change refusal, tests run after apply, failed changes reported honestly | PostgreSQL + Neo4j (+ Docker for one case) |
| Testing loop | First-pass success, fail→fix→approve→recover, iteration limit, wall-clock timeout, approval never bypassed | PostgreSQL + Neo4j |

**"Tool selection" is scoped to what this project actually has**, not a capability that doesn't exist: there's no autonomous LLM-driven tool-calling loop in this codebase (the agent graph routes deterministically via `classify_task`, not an LLM choosing a tool). Agent evaluation instead checks that deterministic routing invokes only the capabilities appropriate to a task type, via the same `execution_log` entries the agent already produces.

**RAG evaluation is fully deterministic**, computed before any LLM runs — `answer_is_non_empty` is the only thing checked about the generated text itself. An optional `--llm-judge` flag adds a real Anthropic-scored metric (`llm_judge_score`), isolated behind its own `LLMJudge` interface, **requires a real `LLM_API_KEY`**, never runs by default, and never gates pass/fail — always reported as an LLM's own judgment, not a deterministic measurement.

**Modification and testing-loop evaluation reuse Phase 9/10's real mechanisms** (`ModificationService`, `AgentService`, the real graph nodes) rather than simulating them — one modification case runs a real Docker container end-to-end (skipped honestly if Docker isn't reachable); the testing-loop cases use a scripted, non-Docker `TestRunner` stand-in (`evaluation/scripted_test_runner.py` — deliberately NOT in `backend/sandbox/`, which ships no fake implementation at all) to isolate the loop's bounded-iteration/timeout/approval-per-retry guarantees from sandbox variability.

**No new database:** reports export to JSON on disk, which is sufficient for "diff one run against a later one" — no query/index/concurrent-write need that would justify adding infrastructure for a handful of small reports.

**Exact results from a real run in this environment:** all **17/17 cases pass**, 0 skipped (PostgreSQL, Neo4j, and Docker are all reachable here). `--llm-judge` was not exercised live (no `LLM_API_KEY` present) — its response-parsing logic is unit-tested with a fake provider instead.

**What's genuinely tested locally:** 58 tests in `tests/evaluation/` — pure unit tests for metric math, dataset structure, and report rendering, plus integration tests for every runner against real services, including tests that prove each runner correctly reports `passed=False` for a deliberately-wrong expectation (not only ever reports success).

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/evaluation                        # 58 tests; DB/Neo4j/Docker-backed ones skip cleanly if unreachable
.venv/bin/python backend/scripts/run_evaluation.py                  # prints the full report; exit code reflects pass/fail
.venv/bin/python backend/scripts/run_evaluation.py --json out.json  # also export raw results as JSON
.venv/bin/python backend/scripts/run_evaluation.py --llm-judge       # adds a real-LLM RAG metric (requires LLM_API_KEY)
```
