# Architecture

This document describes the current and planned architecture of the AI Software Engineering Agent.

Almost everything described here is **PLANNED**. Sections are explicitly labeled so it is always clear what exists versus what is designed but not built.

## Project Layers

### Frontend
**Status: PLANNED**

User-facing interface for interacting with the agent: submitting repositories, asking questions, reviewing proposed changes, and approving or rejecting modifications. No framework has been chosen or initialized yet.

### Backend API
**Status: PARTIALLY IMPLEMENTED**

The `backend/` directory now hosts real Python code (the ingestion subsystem below), but there is no HTTP API yet — no framework has been chosen or initialized. The ingestion subsystem is used today as a plain Python library, invoked directly (e.g. from tests or the manual demo script), not through an API endpoint.

### Repository Ingestion
**Status: IMPLEMENTED (Phase 1, local paths only)**

Located at `backend/ingestion/`. Given a local repository path, it recursively discovers files, applies a configurable ignore/filter policy, detects languages deterministically by extension, and produces a structured `IngestionResult` (repository-level stats plus per-file metadata plus ignored-file reasons plus non-fatal errors).

Components:
- `scanner.py` — recursive traversal via `os.walk`, pruning ignored directories and skipping symlinks; records inaccessible paths as non-fatal errors rather than raising.
- `filters.py` — pure decision logic: generated-artifact glob patterns, known binary extensions, configurable max file size, and a content sniff (first ~1KB, null-byte heuristic) for extensions not otherwise recognized as text.
- `language.py` — a static extension -> language map (Python, JavaScript, JSX, TypeScript, TSX, Java, C, C++, Go, HTML, CSS, SQL, JSON, YAML) and an extension/language -> category map (source / markup / config / documentation / other).
- `models.py` — dependency-free dataclasses: `FileMetadata`, `RepositoryMetadata`, `IgnoredFile`, `LanguageStats`, `IngestionResult`.
- `service.py` — `IngestionService`, the only component that knows the order of operations (scan -> filter -> detect -> collect -> result).

Explicitly out of scope for Phase 1: GitHub cloning (Phase 11), reading file contents beyond the binary-detection sniff, and anything from parsing onward.

### Code Parsing
**Status: IMPLEMENTED (Phase 2)**

Located at `backend/parsing/`. Consumes a Phase 1 `IngestionResult` and extracts functions, classes, imports, and line ranges from every source file it has a parser for.

Components:
- `python_parser.py` — stdlib `ast`-based extraction for Python.
- `languages.py` — Tree-sitter grammar loading (cached) plus one extraction function per language family (JS/JSX/TS/TSX share one, since TS's grammar is a superset for the constructs extracted; Java; Go; C/C++).
- `ts_utils.py` — shared, dependency-free Tree-sitter traversal helpers (node text, line ranges, descendant search) used by every language extractor.
- `treesitter_parser.py` — the single entry point that loads the right grammar, parses, dispatches to the right extractor, and turns any failure into a recorded `parse_error` rather than an exception.
- `models.py` — `ImportEntity`, `ClassEntity`, `FunctionEntity`, `ParsedFile`, `ParsingResult`.
- `service.py` — `ParsingService`, orchestrating "for each Phase 1 file classified as source: read it, parse it, collect the result."

**Why Tree-sitter over per-language AST libraries for non-Python languages:** the alternative would be a separate, differently-shaped parser dependency per language (e.g. Esprima/Babel for JS, javalang for Java, go/ast bindings for Go) with inconsistent APIs, or hand-rolled regex extraction (fragile against real syntax). Tree-sitter gives one consistent node/field API across all of them, which is exactly what the shared JS/TS extractor and the `ts_utils.py` helpers rely on. Python is the one language kept off Tree-sitter because the standard library `ast` module is already exact, dependency-free, and gives line ranges directly — reaching for Tree-sitter there would be an unjustified dependency.

Explicitly out of scope for Phase 2: SQL entity extraction (functions/classes don't map cleanly onto SQL — recorded as skipped, not faked), full cross-file symbol resolution (e.g. resolving an import to the class it points at — that is Phase 5's job), and decomposing parameters into typed (name, type) pairs.

### Vector Search Foundation
**Status: IMPLEMENTED (Phase 3, local embedding provider LOCAL TESTED; OpenAI provider REQUIRES CREDENTIALS)**

Located at `backend/vectorstore/`. Chunks Phase 2's parsed output, embeds each chunk via a pluggable provider, and stores/searches the result in PostgreSQL + pgvector.

Components:
- `chunking.py` — one `CodeChunk` per class/function (sliced from source using Phase 2's line ranges), with a whole-file fallback for source files that produced no symbols (currently only SQL).
- `embeddings/base.py` — the `EmbeddingProvider` interface (`embed(texts) -> vectors`).
- `embeddings/openai_provider.py` — production provider, OpenAI `text-embedding-3-small` (1536 dims). Requires `EMBEDDING_API_KEY`; raises `MissingCredentialsError` with a clear message if unset. Not exercised against a live API in this environment (no key present) — its request/response handling is unit-tested against a mocked client.
- `embeddings/local_provider.py` — `DeterministicLocalEmbeddingProvider`, a hash-based, **non-semantic** stand-in for local dev/tests. Same interface and dimension as the OpenAI provider, so it can exercise the exact same storage/search code path without credentials or network access. Explicitly documented as not usable for demonstrating real retrieval quality.
- `schema.sql` / `migrations.py` — two tables (`repositories`, `code_chunks`), applied idempotently. No migration framework (e.g. Alembic) yet — the schema is still evolving and `CREATE ... IF NOT EXISTS` is sufficient and honest about that.
- `store.py` — `VectorStore`: raw parameterized SQL via `psycopg` (no ORM — unjustified for two tables), cosine-distance similarity search using pgvector's `<=>` operator over an HNSW index, with metadata filtering by repository/language/chunk type.
- `service.py` — `IndexingService`, wiring ingestion (Phase 1) → parsing (Phase 2) → chunking → embedding → storage into one call.

**Local environment used for this build:** PostgreSQL 18 (Homebrew) with pgvector 0.8.6, in two databases — `ai_swe_agent` (dev) and `ai_swe_agent_test` (automated tests, truncated between test runs). Both are dedicated to this project and separate from this machine's other local databases. The automated test suite (`tests/vectorstore/`, 18 tests) skips cleanly rather than failing if no Postgres is reachable, since that shouldn't be assumed on every machine this project runs on.

**Why PostgreSQL + pgvector over a dedicated vector database:** the project's own technology policy rules out adding infrastructure (Pinecone/Weaviate/Elasticsearch/Redis) without a specific engineering reason, and none exists yet — a single Postgres instance already serves both structured application data and vector search at this project's scale, with no operational cost of running a second system.

Explicitly out of scope for Phase 3: real semantic retrieval quality (requires a real API key, which this environment does not have), embedding documentation/config files (only "source"-category files are chunked), and a migration framework (deferred until the schema stabilizes).

### AI / Agent Layer
**Status: PLANNED**

Coordinates retrieval, reasoning, and tool use. Will be implemented as a stateful workflow (see Agent Layer below), not a single autonomous loop.

### Retrieval Layer (Code RAG)
**Status: IMPLEMENTED (Phase 4; retrieval/ranking LOCAL TESTED, LLM answer generation REQUIRES CREDENTIALS)**

Located at `backend/rag/`. Pipeline: question → query embedding → vector retrieval (Phase 3) + keyword retrieval → merge/rank → bounded context assembly → LLM → grounded answer with source citations.

Components:
- `keyword_search.py` — PostgreSQL full-text search over `code_chunks.content`, using an OR of the question's word tokens (`to_tsquery`) rather than `plainto_tsquery`'s implicit AND, since requiring every word in a natural-language question to match is too strict for this use case. Backed by a GIN index (`schema.sql`).
- `ranking.py` — merges vector hits (Phase 3) and keyword hits into one list, scored `0.7 * vector_similarity + 0.3 * keyword_score` (documented as a deliberately simple starting point; Phase 6 is expected to add graph evidence to this).
- `context.py` — assembles a size-bounded context string (default 8000 chars) from the top-ranked candidates, returning exactly which candidates were included so citations never reference something the LLM wasn't shown.
- `prompt.py` — builds the grounded-answer prompt; documents honestly that nothing here *verifies* the model complied with "answer only from context" — that's a Phase 12 evaluation concern.
- `llm/base.py`, `llm/anthropic_provider.py`, `llm/stub_provider.py` — the same abstraction pattern as Phase 3's embedding providers: a real provider requiring `LLM_API_KEY` (unit-tested via a mocked client, not exercised live in this environment) and an explicitly-non-LLM deterministic stub used to test the full pipeline without credentials.
- `service.py` — `RAGService`, wiring retrieval → ranking → context → LLM into one `answer(question, ...)` call, with metadata filtering by repository/language/chunk type passed through to both retrieval methods.

**What's genuinely tested locally vs. what needs external credentials:** retrieval, ranking, context assembly, and the full pipeline (with the stub LLM) run against real PostgreSQL in `tests/rag/` (21 tests). Only `AnthropicLLMProvider` actually calling a live model is untested in this environment, for the same reason as Phase 3's OpenAI provider: no key is present.

Explicitly out of scope for Phase 4: retrieval by exact commit/version (Phase 1/2 don't track git history yet), and any verification that the LLM's free-text answer is actually faithful to the retrieved context (source citations are accurate regardless, since they're built from retrieval metadata, not the model's claims).

### Knowledge Graph
**Status: IMPLEMENTED (Phase 5, LOCAL TESTED against a real Neo4j instance)**

Located at `backend/graph/`. Builds a Neo4j graph from Phase 1 + Phase 2 output: `Repository`, `Folder`, `File`, `Class`, `Function`, `Module` nodes; `CONTAINS`, `DEFINES`, `IMPORTS`, `RESOLVES_TO`, `INHERITS` relationships — every one derived from data Phase 1/2 actually extracted, never inferred by name-matching alone where that would be unreliable.

Components:
- `client.py` — thin wrapper over the official `neo4j` driver; no query-builder/ORM layer, since Cypher is already the right level of abstraction for graph traversal.
- `schema.py` — idempotent composite-uniqueness constraints (verified to work on Neo4j 5 **Community** Edition, not just Enterprise), keyed by `(repository_root_path, ...)` so re-indexing a repository never duplicates nodes and different repositories never collide.
- `resolution.py` — best-effort import-string → file resolution (Python, JS/TS/JSX/TSX relative imports, Java dotted imports) and base-class-name → class resolution (only when unambiguous). Explicitly documents what is *not* attempted (Go, C/C++ import resolution) and why, rather than faking it.
- `builder.py` — `GraphBuilder`, using `UNWIND`-batched Cypher writes per node/relationship type rather than one round-trip per item.
- `queries.py` — read-only Cypher queries answering exactly the kind of question vector search can't: `get_file_dependencies`, `get_importers_of_file`, `get_class_ancestors` (transitive), `find_symbol`, `get_repository_summary`.

**Explicitly NOT created: `CALLS`, `USES`, `DEPENDS_ON` relationships.** Phase 2 extracts definitions (functions, classes, imports), not call-sites inside function bodies, so there is no real data to build these from. Per this project's own instruction not to hallucinate `CALLS` relationships, they are left out entirely rather than approximated from name-matching — they become possible once a future phase adds call-site extraction.

**Why Neo4j over encoding relationships in PostgreSQL:** the questions this graph answers (transitive inheritance chains, dependency traversal, reverse lookups) are exactly what a graph database is built for — recursive traversal in Cypher is a few lines; the same query in SQL needs recursive CTEs per relationship type and gets unwieldy fast as more relationship types are added (Phase 6+ will add more). This is the one piece of the technology policy's "graph: Neo4j" guidance that has a genuine, demonstrated engineering reason behind it, not just because the technology list called for it.

Explicitly out of scope for Phase 5: `CALLS`/`USES`/`DEPENDS_ON` (see above), Go and C/C++ import resolution (documented in `resolution.py`), and resolving Python relative imports (`from . import x` — Phase 2's Python parser doesn't capture the AST's relative-import `level`, so these are treated as absolute and typically fail to resolve, which just means no edge, not a wrong one).

### Hybrid Retrieval
**Status: IMPLEMENTED (Phase 6, LOCAL TESTED against real PostgreSQL + Neo4j together)**

Located at `backend/hybrid/`. Extends Phase 4's vector+keyword retrieval with a third evidence source: one-hop expansion through the Phase 5 graph from the top seed candidates (class methods, inheritance neighbors, and symbols defined in files the seed depends on).

Components:
- `graph_expansion.py` — `find_related_symbols` (pure Neo4j traversal, one hop, from a seed's chunk_type/qualified_name) and `fetch_chunks_by_symbol` (resolves graph-found symbols back to real `code_chunks` rows in Postgres via a dynamic OR of exact `(relative_path, qualified_name)` pairs — chosen over two independent `ANY(...)` array conditions, which would incorrectly match cross-combinations between different pairs).
- `ranking.py` — merges vector, keyword, and graph evidence: `0.5 * vector_similarity + 0.2 * keyword_score + 0.3 * graph_score`, with every chunk's `found_via` tuple recording exactly which method(s) surfaced it — this is what "do not simply concatenate arbitrary results" means in practice: every inclusion is traceable to a reason.
- `service.py` — `HybridRAGService`, reusing Phase 4's `build_context`/`build_prompt`/LLM-provider machinery unchanged (only the retrieval and ranking stages are new).

**Worked example, actually built and tested, not just described:** `login_route` (in `routes.py`) matches by vector/keyword. `routes.py` imports `auth_service.py` (resolved via Phase 5's `RESOLVES_TO`), and `auth_service.py` defines both `login_user` and `generate_jwt`. Graph expansion follows that one hop and pulls in `generate_jwt` — a function `login_route` never calls directly (it calls `login_user`, which calls `generate_jwt`) and the question never mentions — tagged `found_via=("graph:dependency_symbol",)` (or combined with vector/keyword if those also matched). Verified in `tests/hybrid/test_graph_expansion.py` with controlled seeds (not dependent on the non-semantic local embedding provider's ranking) and end-to-end in `tests/hybrid/test_service.py`.

**A real architectural seam, documented rather than hidden:** Phase 3 (Postgres) keys a repository by an integer `repository_id`; Phase 5 (Neo4j) keys it by `root_path`. The two subsystems were built independently and don't share one identifier. `HybridRAGService.answer()` requires the caller to supply both rather than papering over this with an implicit lookup. Unifying them (e.g. storing `repository_id` as a property on the Neo4j `Repository` node) is a reasonable future refinement — not done here, since it would mean reopening already-tested Phase 3/5 code purely for convenience, not correctness.

Explicitly out of scope for Phase 6: tuning the 0.5/0.2/0.3 weights against a real evaluation dataset (Phase 12), and expanding more than one hop out from a seed (bounded by design, not a limitation to fix — unbounded traversal on a large repository would risk pulling in the entire dependency graph as "context").

### Agent Layer
**Status: IMPLEMENTED (Phase 7, updated in Phase 9; LOCAL TESTED against real PostgreSQL + Neo4j)**

Located at `backend/agent/`. A stateful **LangGraph** workflow — not one giant autonomous agent — implementing this topology (the `modify` branch was reordered in Phase 9; see below):

```
Task Analyzer -> Planner -> Repository Search -> Graph Search -> Code Analyzer
                                                        ^              |
                                                        |   (retry, bounded by MAX_RETRIES)
                                                        +--------------+
                                                                       v
                                                                   Decision
                                    Answer <---+----> Test          Propose Change
                                                                          |
                                                              Human Approval (interrupt,
                                                              shown the actual diff)
                                                                          |
                                                                    Apply Change
                                                          (writes only if approved; the one
                                                           function in this project that does)
```

Components:
- `state.py` — `AgentState`, a plain `TypedDict` (not dataclasses) so LangGraph's checkpointer can persist it verbatim across the human-approval pause; `execution_log` uses an `operator.add` reducer so each node's summary appends rather than overwrites.
- `classification.py` — deterministic, rule-based task classification (`answer` / `modify` / `test`). Deliberately not an LLM call: keeps routing fully testable without `LLM_API_KEY` and keeps the decision inspectable.
- `nodes.py` — one function (or factory, for nodes needing a service) per graph node. Reuses Phase 3-6's building blocks directly (`vector_store.similarity_search`, `keyword_search`, `merge_and_rank` from both `rag` and `hybrid`, `build_graph_candidates`, `build_context`, `build_prompt`) rather than re-implementing retrieval logic.
- `graph.py` — builds and compiles the `StateGraph`: conditional edges for the bounded retry loop and the four-way decision routing.
- `service.py` — `AgentService.run()` / `.resume()`, hiding LangGraph's `Command(resume=...)` mechanics behind a plain two-method API keyed by a caller-provided `thread_id`.

**Explicit state, not implicit conversation history** — `AgentState` carries every intermediate result (search hits, graph candidates, ranked candidates, decision, approval) as plain data, not as an opaque running transcript.

**Conditional routing based on task type** — `decision_node` reads `task_type` and routes to `answer`, `test`, or `human_approval` (which gates `modify` — the one consequential action among the three, per this project's own human-approval-for-consequential-actions rule).

**Bounded retries, provably terminating** — `code_analyzer` only sets `should_retry=True` when `retry_count < MAX_RETRIES` (2), and `should_retry` is recomputed fresh on every pass (never based on stale state), so the retry loop cannot run more than `MAX_RETRIES` times regardless of repository content. Tested directly: an empty-repository run produces exactly 2 "retrying" log entries, then an honest "no context found" answer.

**Real human approval, not simulated** — `human_approval_node` calls LangGraph's `interrupt()` with the actual proposed diff (generated by Phase 9's `propose_change` node just before), which actually pauses the graph and returns control to the caller; work only continues after an explicit `resume(thread_id, approved=...)` call. Both paths are tested end-to-end: rejection leaves the file untouched, approval applies the change via `apply_change_node` (Phase 9).

**No hidden chain-of-thought** — every node appends one short, safe summary to `execution_log` (e.g. `"Analyzing dependencies... found 2 related symbol(s)."`), matching this project's own observability principle. Nothing resembling raw model reasoning is exposed.

**Update (Phase 9):** the `modify` path described above was a stub when Phase 7 was built; Phase 9 wired in the real mechanism. See the "Code Modification" section below for what changed and why the approval ordering was corrected in the process.

**Update (Phase 10):** the `test` branch is no longer a stub — it runs the repository's test suite in the Phase 10 sandbox and reports real pass/fail/output. More significantly, `apply_change` no longer ends the graph: an applied change now flows into `run_tests_after_apply`, which runs the sandbox again to verify it, and — only on a real test failure, and only up to `MAX_FIX_ITERATIONS` (2) fix attempts within a `MAX_LOOP_SECONDS` (300s) wall-clock budget — loops back to `propose_change` with an instruction built from the actual failure output. Every single one of those fix attempts still goes through a fresh `human_approval` interrupt; the loop cannot bypass approval and cannot run unboundedly, by construction (see "Testing Loop" below for the full detail). `AgentState.applied` (distinct from `approved`) gates entry into this path, so a rejected or a failed-to-apply change correctly never enters test verification.

### Tools
**Status: IMPLEMENTED (Phase 8, read-only; 7 of 9 fully functional)**

Located at `backend/tools/`. Formalizes what Phase 7's agent nodes were calling directly into a proper tool abstraction with declarative schemas, validation, and authorization boundaries.

- `schemas.py` — **Pydantic** (not dataclasses, unlike the rest of the backend) input/output models per tool. Justified specifically here: tool input may come from an LLM or external caller and needs runtime validation with clear errors, plus JSON Schema generation for future LLM tool-calling registration — a concrete need dataclasses don't meet.
- `registry.py` — `ToolRegistry.invoke(name, raw_input)` is the single choke point: validates input, calls the handler, and turns every expected failure (bad input, an authorization boundary, an intentionally-unavailable capability) into a structured `ToolResult(success, data, error)` rather than an uncaught exception.
- `security.py` — `resolve_safe_path`, a real path-traversal guard used by every path-taking tool. Verified against actual traversal attempts, not just asserted.
- `file_tools.py` — `list_files` (fresh filesystem scan every call, not a possibly-stale index), `read_file` (size-capped, binary-rejecting), `analyze_code` (Phase 2 parsing for one file).
- `search_tools.py` — `search_code` (Phase 3+4), `search_symbol` (Phase 5, exact name lookup).
- `graph_tools.py` — `graph_query` (a **fixed enum** of pre-built Cypher queries — `repository_summary` / `importers` / `class_ancestors` — never raw Cypher text from a caller, since that would be the graph-database equivalent of exposing arbitrary shell execution), `get_dependencies` (Phase 5), and `get_callers`/`get_callees`.

**`get_callers`/`get_callees` are honestly unavailable, not approximated.** Phase 5's graph has no `CALLS` relationship (Phase 2 doesn't extract call-sites), so there is no real data to answer "who calls this?" from. Both tools return `available=False` with a clear, specific reason rather than guessing via name-matching — a plausible-looking wrong answer would be worse than an honest gap, and an agent has no way to tell the two apart otherwise.

**No write or execute capability exists in this package**, by construction: there is no `write_file`, no `create_patch`, no `run_tests`, no subprocess/shell invocation anywhere in `backend/tools/`. Those are Phase 9/10's concern and, per this project's agent design, require the human-approval gate Phase 7 already built before anything is written.

### Code Modification
**Status: IMPLEMENTED (Phase 9, mechanism LOCAL TESTED; content quality REQUIRES CREDENTIALS)**

Located at `backend/modification/`. Implements the safe modification workflow and wires it into Phase 7's agent for real, replacing that phase's stub.

- `file_finder.py` — `find_affected_file` reuses Phase 3+4's vector+keyword retrieval and ranking unchanged: "which file does this instruction affect" is the same problem as "which code answers this question."
- `change_generator.py` — `ChangeGenerator.generate` asks the LLM for the complete new file content. With `StubLLMProvider`, the result is a fixed placeholder, not valid code — proves the pipeline, not generation quality.
- `diff.py` — `generate_unified_diff` via the standard library `difflib` (the same format `git diff` uses; no dependency needed).
- `service.py` — `ModificationService.propose_change` (find file, generate content, diff — never writes) and `.apply_change` (the **only function in this entire project that writes to a repository file**). `apply_change` re-reads the target file immediately before writing; if it differs from what the proposal was generated against, it raises `StaleChangeError` rather than silently overwriting a concurrent edit — verified directly in tests.
- Reuses Phase 8's `tools.security.resolve_safe_path` for the same path-traversal guard, rather than a second implementation of it.

**A correctness fix made during this phase, applied immediately since nothing had been committed yet:** Phase 7's original topology asked for approval *before* any change existed to show (`decision -> human_approval -> modify`), which meant "approve blindly, then see a stub." Phase 9's own spec is explicit that the diff comes before approval (`Generate -> Create patch -> Show diff -> HUMAN APPROVAL -> Apply`), so `agent/graph.py` was reordered to `decision -> propose_change -> human_approval (shown the real diff) -> apply_change`. The three Phase 7 tests that asserted "approved modification still makes no changes" were updated to assert the opposite, since that's now true and correct.

**What's genuinely tested locally vs. what needs a real LLM:** the full mechanism — file-finding (against real PostgreSQL), diff generation, the approval gate, applying exactly once, and refusing a stale write — is tested in `tests/modification/` (7 tests, isolated `tmp_path` fixtures, never the real project repository) and end-to-end through the agent in `tests/agent/`. Real, meaningful code changes require `AnthropicLLMProvider` and a real `LLM_API_KEY` — not present in this environment.

Explicitly out of scope for Phase 9: test execution after applying a change (Phase 10's concern, see below) and multi-file changes (one proposal always targets exactly one file — unchanged by Phase 10).

### Sandbox
**Status: IMPLEMENTED (Phase 10, LOCAL TESTED against real Docker)**

Located at `backend/sandbox/`. An isolated Docker-container execution environment for running a target repository's own test suite. Arbitrary repository code never runs directly on this host — every test run happens inside a container, verified directly (not just asserted) in `tests/sandbox/test_docker_integration.py` and `backend/scripts/manual_sandbox_demo.py`.

Components:
- `command_detection.py` — `detect_test_command`, a pure function reusing Phase 1's `IngestionService` for file discovery rather than re-implementing directory traversal. Only Python repositories using pytest are supported this phase (detected via `pytest.ini`/`setup.cfg`/`pyproject.toml`/`tox.ini` or `test_*.py`/`*_test.py` files) — an honest, documented scope limit, the same "implement the mechanism honestly, don't fake the capability" pattern as Phase 8's `get_callers`/`get_callees`.
- `models.py` — `SandboxLimits` (timeout, memory, CPU, PID count, network on/off) and `TestRunResult` (command, exit code, stdout, stderr, timed-out flag, duration; `.passed` is `exit_code == 0 and not timed_out`).
- `base.py` — the `TestRunner` abstract interface. Deliberately has exactly one real implementation: unlike the embedding/LLM provider abstractions, this project must never ship an "unsandboxed" alternative, since "repository code never runs on the host" is a security invariant, not a swappable backend choice. Test doubles for this interface live only in the test suite (`tests/agent/conftest.py::FakeTestRunner`), never in `backend/`.
- `docker_runner.py` — `DockerTestRunner`, the only real implementation. Shells out to the `docker` CLI via `subprocess` rather than adding the `docker` Python SDK as a dependency — consistent with this project's existing preference for the official low-level client over an extra abstraction layer (raw SQL via `psycopg`, raw Cypher via the `neo4j` driver, no ORM/query builder anywhere), and Docker Desktop is already part of this project's local environment (Neo4j itself runs in a container).
- `docker/python-test.Dockerfile` — a minimal `python:3.11-slim` image with only `pytest` preinstalled, built locally as `ai-swe-agent-sandbox-python:latest` (never pushed to a registry). Deliberately does not try to be a general-purpose Python environment: a target repository's own extra dependencies (numpy, requests, ...) are not installed, since installing them would require network access the sandbox disables by default. This is a real, documented limitation, not something faked.

**Isolation model, verified directly against real containers, not just configured:**
- The repository is copied into a fresh temporary directory first; that COPY — never the real repository — is bind-mounted in. `tests/sandbox/test_docker_integration.py::test_real_container_does_not_mutate_the_original_repository` proves a file a test writes never appears on the host.
- `--network none` by default. `test_real_container_has_no_network_access` proves a real socket connection attempt from inside the container fails.
- `--read-only` root filesystem plus a small writable `/tmp` tmpfs and the writable workspace mount. `test_real_container_root_filesystem_is_read_only` proves a write to `/etc/` fails.
- `--cap-drop ALL --security-opt no-new-privileges`, plus `--memory`/`--cpus`/`--pids-limit` resource caps.
- No `-e`/`--env-file` flag is ever passed — the container never sees this host's environment variables or `.env` secrets, only whatever the image itself defines.
- A hard `subprocess.run(..., timeout=...)` enforces `SandboxLimits.timeout_seconds`; on timeout the container is force-killed (`docker kill`) as a best-effort cleanup, since `--rm` alone does not stop a container that is still running — `test_real_container_is_killed_and_removed_on_timeout` proves no container is left behind.

**What's genuinely tested locally:** all 19 tests in `tests/sandbox/` run for real in this environment — 13 pure/mocked unit tests (command detection, `subprocess.run` command construction and error mapping, no Docker involved) plus 6 real-Docker integration tests exercising an actual container for each isolation property above. The integration tests and `requires_sandbox_image` skip cleanly rather than fail if Docker isn't reachable or the image hasn't been built (see `tests/sandbox/conftest.py`) — the same "LOCAL TESTED vs REQUIRES EXTERNAL SERVICE" distinction as every other phase's external dependency.

Explicitly out of scope for Phase 10's sandbox: non-Python test runners (`npm test`, etc. — no repository or worked example in this project needs them yet), per-test result parsing (only the overall exit code is used — a JUnit-XML parser would be a reasonable future addition, not faked here), and installing a target repository's own third-party dependencies inside the container (would require enabling network access, which defaults to off for security).

### Testing Loop
**Status: IMPLEMENTED (Phase 10, LOCAL TESTED against real PostgreSQL + Neo4j with a mocked test runner, plus real Docker end-to-end via the manual demo)**

Extends Phase 9's `apply_change` with real test verification, and a bounded, human-approved fix loop, using the Phase 10 sandbox (see "Sandbox" above) as the test runner.

```
apply_change -[applied?]-> run_tests_after_apply -[passed]-> END ("Tests: passed.")
                                                  -[no test command detected]-> END (honest, not faked)
                                                  -[failed, budget remains]-> propose_change (new instruction:
                                                                                the actual failure output)
                                                                           -> human_approval (fresh interrupt)
                                                                           -> apply_change -> run_tests_after_apply -> ...
                                                  -[failed, budget exhausted]-> END ("Giving up after N fix attempt(s)")
```

- **Two independent bounds, either one stops the loop** (`agent/state.py`): `fix_iteration < MAX_FIX_ITERATIONS` (2) and a wall-clock `loop_deadline` set once, on the first post-apply test run, to `time.monotonic() + MAX_LOOP_SECONDS` (300s) — persisted across iterations so it bounds the *whole* loop, not any single pass. An iteration cap alone would still allow a slow iteration to run arbitrarily long; a time cap alone would still allow many fast-failing iterations within the budget. Both are real and independently tested (`tests/agent/test_testing_loop.py::test_time_budget_exhausted_stops_the_loop_even_within_iteration_limit` forces the time budget to 0 and proves the loop stops on the very first failure, well under the iteration cap).
- **The human-approval gate is never bypassed, no matter how many fix attempts are made** — every iteration re-enters `propose_change -> human_approval`, and `human_approval_node`'s interrupt payload says which fix attempt it is (`"Fix attempt 1/2 requires human approval..."`) so the reviewer isn't confused by a diff that doesn't match the original request. `test_a_fix_attempt_still_requires_its_own_human_approval` proves rejecting a fix attempt stops the loop immediately, with no further test run.
- **`modification_instruction`** (`agent/state.py`) drives `propose_change_node`: `None` on the first pass (the original question is used), overwritten by `run_tests_after_apply_node` with an instruction built from the actual failing test's stdout/stderr (bounded to 2000 characters — the same "bounded context" principle as `rag/context.py`, applied to sandbox output) on every retry, so each fix attempt targets the real failure.
- **`applied`, not `approved`, gates entry into test verification** (`agent/graph.py::_route_after_apply`) — an approved change can still fail to apply (e.g. `StaleChangeError`), and that case must not run tests it never actually applied.
- **No test command detected, or the sandbox itself unavailable, are reported honestly** rather than treated as a pass or a silent no-op: `"the change was applied but not verified"` / `"Tests: could not run (...)"`.

**What's genuinely tested locally vs. what needs real Docker:** the routing/bounding logic itself — iteration cap, time cap, the approval-gate-per-retry guarantee, the instruction-rewriting on retry — is tested in `tests/agent/test_testing_loop.py` (4 tests, real PostgreSQL + Neo4j, `FakeTestRunner` standing in for the sandbox so the tests are deterministic and don't depend on Docker or on the stub LLM ever producing code that could plausibly pass). The full loop against a REAL Docker container and real pytest execution is demonstrated end-to-end in `backend/scripts/manual_agent_demo.py`'s scenario 4: a repository with a real test, a real applied change (the stub LLM's placeholder text, which is not valid Python), two real sandboxed test failures, two human-approved fix attempts, and an honest give-up — proving every piece of this loop works together against real infrastructure, not just against mocks.

Explicitly out of scope for Phase 10's testing loop: automatically retrying the *original* request text (a raw retry without incorporating the failure would be far less likely to converge — see this project's own explicit design), and any bound on the quality of a fix attempt beyond the iteration/time caps (a real LLM could still exhaust the fix-attempt budget on a genuinely hard bug — the loop's job is to stop safely, not to guarantee success).

### Database
**Status: PARTIALLY IMPLEMENTED**

PostgreSQL 18 with the pgvector extension (0.8.6) is provisioned locally in this environment: a dedicated `ai_swe_agent` database for development and `ai_swe_agent_test` for the automated test suite — both separate from this machine's other, unrelated local databases. Schema: `repositories` and `code_chunks` (see `backend/vectorstore/schema.sql`). No application-level tables beyond what Phase 3 needs (users, agent_runs, tool_calls, evaluations) exist yet — those arrive with the phases that need them.

### GitHub Integration
**Status: IMPLEMENTED (Phase 11, LOCAL TESTED including real GitHub API + real `git clone` against a public repository; push/PR mechanism-tested with mocks — see below)**

Located at `backend/github_integration/`. Behind one service (`GitHubIntegrationService`), wraps: URL validation, repository metadata retrieval, cloning into a controlled workspace, wiring a clone into the EXISTING Phase 1/2/3/5 pipeline, and branch/commit/push/pull-request operations — the push and PR steps gated exactly like Phase 9's `apply_change` is gated by human approval.

```
URL -> parse_github_url (validate) -> GitHubAPIClient.get_repository (metadata)
    -> resolve_clone_destination (workspace-root-confined) -> GitOperations.clone
    -> [caller runs Phase 1/2/3/5 pipeline unchanged — github_integration/pipeline.py]
    -> GitOperations.create_branch -> GitOperations.commit_all
    -> [push ONLY if authorized=True] -> [pull request ONLY if approved=True]
```

Components:
- `url_validation.py` — `parse_github_url`, deliberately narrow: only `https://github.com/<owner>/<repo>` (optionally `.git`-suffixed or trailing-slashed) is accepted. SSH URLs, other hosts, userinfo-smuggled netlocs, and lookalike hosts (`github.com.evil.com`) are all rejected, not approximated — the owner/repo segments are validated against a strict `[A-Za-z0-9._-]` character set *before* they are ever used to build a filesystem path or an API request, which is what makes the path-traversal property below straightforward to reason about.
- `workspace.py` — `resolve_clone_destination` reuses Phase 8's `tools.security.resolve_safe_path` **unchanged** (not reimplemented) to confine every clone to `workspace_root/<owner>/<repo>`, never elsewhere. Tested directly with an already-malicious `RepositoryReference` (bypassing URL validation entirely) to prove this is real defense-in-depth, not reliant on the first layer alone.
- `api_client.py` — `GitHubAPIClient`, a thin wrapper over `requests` (no PyGithub/githubkit SDK — see `requirements.txt` for the justification) covering exactly the two calls this project needs: `get_repository` and `create_pull_request`. The token is sent only as an `Authorization: Bearer` header, never as a query parameter, and never printed or logged.
- `git_operations.py` — `GitOperations`, shelling out to the `git` CLI via `subprocess` — the same "official CLI over an added SDK" pattern as Phase 10's Docker sandbox. **`GITHUB_TOKEN` never appears in a subprocess argv list** (visible to any process on the host via `ps`); instead it's passed only through the child process's `GIT_TOKEN` environment variable, read at credential time by an inline shell credential helper (`credential.helper=!f() { echo password=$GIT_TOKEN; }; f` — a standard git mechanism, not something this module implements). Verified directly: `tests/github_integration/test_git_operations.py` asserts the literal token string never appears in any constructed command.
- `service.py` — `GitHubIntegrationService`, orchestrating the above. `push_branch` requires `authorized=True`; `create_pull_request` requires `approved=True` — both raise `UnauthorizedActionError` otherwise, mirroring `ModificationService`'s `apply_change` gate exactly. Neither flag is ever defaulted to `True` or inferred.
- `pipeline.py` — `ingest_github_repository`, composing a clone with the **existing, unmodified** `IngestionService` (Phase 1), `ParsingService` (Phase 2), `IndexingService` (Phase 3), and optionally `GraphBuilder` (Phase 5) — nothing here reimplements ingestion, parsing, embedding, or graph building; it only feeds a cloned repository's local path into services that already exist, exactly as `backend/scripts/manual_agent_demo.py` already does for a local path.

**Authentication is `GITHUB_TOKEN` only** (`GitHubIntegrationService(token=...)`, defaulting to `os.environ.get("GITHUB_TOKEN")` if not passed explicitly) — never hardcoded, never read from anywhere else, never required for read-only operations against public repositories (GitHub's REST API and anonymous `git clone` both work without one, at a lower rate limit).

**The token never reaches repository code or the Docker sandbox** — verified directly, not just designed that way: `tests/github_integration/test_token_isolation.py` sets `GITHUB_TOKEN` in the host environment and proves the Phase 10 sandbox's constructed `docker run` command contains neither the token value nor any `-e`/`--env-file` flag at all (the sandbox already passes zero environment variables into a container, by Phase 10's own construction — this test ties that existing guarantee directly to the specific secret this phase introduces).

**Commit authorship is never invented.** `commit_all` requires an explicit `author_name`/`author_email` from the caller — this project must never hardcode or assume an identity to attribute a commit to (see CLAUDE.md's GitHub-identity rules); the author is scoped to that single `git commit` invocation via `-c user.name=... -c user.email=...`, never touching the clone's persistent config or, more importantly, this project's own git identity (a completely separate repository).

**What's genuinely tested locally vs. what needs a real, owned repository:** all 54 tests in `tests/github_integration/` pass in this environment, including real network calls to `api.github.com` and a real `git clone` of `octocat/Hello-World` (GitHub's own canonical example repo) — `test_github_integration.py` and `test_pipeline.py` (the latter also indexing the real clone through the real Phase 3 pipeline against local PostgreSQL). Push and pull-request creation are mechanism-tested with a mocked `GitOperations`/`requests` (`test_service.py`, `test_api_client.py`) and demonstrated being correctly REFUSED without authorization in `manual_github_demo.py` — an actual push or PR is never exercised against `octocat/Hello-World`, since this project doesn't own it and must never write to a repository it doesn't control. Real push/PR execution requires a real `GITHUB_TOKEN` and a repository the caller actually owns, the same "mechanism proven, real destructive action needs real credentials" pattern as Phase 9's `LLM_API_KEY` caveat.

Explicitly out of scope for Phase 11: GitHub Enterprise / self-hosted hosts, SSH-based authentication, GitLab/Bitbucket or any non-GitHub host, and wiring this package into `agent/graph.py`'s LangGraph workflow (this phase builds the capability behind its own service, as requested; agent-workflow integration — e.g. "fix a GitHub repo end-to-end, then open a PR" — is a natural next step, not implemented here, per this project's own phase-control discipline of only building what was explicitly requested).

### Evaluation
**Status: IMPLEMENTED (Phase 12, all 17 cases LOCAL TESTED and passing against real PostgreSQL + Neo4j + Docker in this environment; optional LLM-judge metric REQUIRES CREDENTIALS)**

Located at `backend/evaluation/`. A real, quantitative evaluation framework covering retrieval, RAG, agent behavior, code modification, and the testing loop — run through the actual Phase 1-10 code, never simulated or reimplemented. Every number below is produced by running `backend/scripts/run_evaluation.py`, not hand-typed; re-running it reproduces the same structure (though not necessarily bit-identical latencies).

**Package structure**, mirroring this project's separation-of-concerns convention:
- `models.py` — `MetricResult` (one measured value, `passed=None` when purely informational) and `CaseResult`/`EvaluationReport` (one case's outcome; a report's aggregation properties).
- `metrics.py` — pure functions (`recall_at_k`, `precision_at_k`, `reciprocal_rank`/`mean_reciprocal_rank`, `hit_rate`) — plain lists/sets in, a float out, no service dependency, unit-tested directly.
- `dataset.py` — the fixed benchmark: one small, human-inspectable sample repository (`build_sample_repository`, ~15 lines across 4 files) plus explicit `RetrievalCase`/`RAGCase`/`AgentCase` instances, each with `expected_*` fields derived by reading the repository's actual content, never by running the system and recording whatever it returned.
- `runners/` — one module per evaluation area (`retrieval.py`, `rag.py`, `agent.py`, `modification.py`, `testing_loop.py`), each building its own temporary repository, running the REAL Phase 1-10 services against it, computing metrics, and cleaning up afterward (scoped `DELETE`s by the exact `repository_id`/`root_path` it created — never a blanket `TRUNCATE`, since a caller may run this against a real development database with other data in it).
- `environment.py` — `postgres_available`/`neo4j_available`/`docker_available`: the same "skip honestly, don't fail" checks as this project's pytest fixtures, usable outside pytest so `run_evaluation.py` behaves identically.
- `scripted_test_runner.py` — `ScriptedTestRunner`, a deterministic, non-Docker stand-in for `sandbox.base.TestRunner`, used only by the testing-loop runner to isolate the fix-loop's ROUTING logic from whether a stub LLM's placeholder text happens to pass or fail a real test suite (a separate, already-covered concern). Lives here, not in `backend/sandbox/`, since that package deliberately ships no fake `TestRunner` at all (see its own docstring) — this is evaluation tooling, never a sandbox alternative.
- `llm_judge.py` — an optional `LLMJudge` interface plus `AnthropicLLMJudge`, isolated exactly like every other pluggable provider in this project. Never constructed unless a caller explicitly opts in (`run_evaluation.py --llm-judge`), and never gates pass/fail — its score is always reported as an LLM's own judgment, not a deterministic measurement.
- `report.py` — renders a human-readable text report (with per-category MRR aggregation for retrieval) and an optional JSON export. No new database: a JSON file is sufficient for "persist one run to diff against a later one" — there's no query/index/concurrent-write need that would justify Postgres for a handful of small reports, per this project's own "don't add infrastructure without a real reason" policy.
- `suite.py` — `run_full_suite`, wiring every runner together with the availability checks, producing explicit `skipped` results (never silently dropped categories) when a service isn't reachable.

**What each metric measures and why it's useful:**
- **Recall@K** — of everything actually relevant, how much did the top K results include? Answers "are we missing relevant code?"
- **Precision@K** — of the top K results, how many were actually relevant? Answers "how much noise is the requester wading through?" — the metric that most clearly exposes ranking-quality differences on a small corpus (see the caveat below).
- **Reciprocal rank / MRR** — how far down the list is the *first* relevant result? The metric that matters most when a user only looks at the top hit or two.
- **Hit rate** — did we surface anything relevant at all, anywhere in the results? The loosest, most forgiving signal, and the one this suite gates pass/fail on for the methods that are meaningful in this environment (see below).

**An honest, load-bearing caveat about the numbers this suite produces:** retrieval runs by default with `DeterministicLocalEmbeddingProvider` — the same hash-based, explicitly non-semantic stand-in used throughout this project's own test suite (no `EMBEDDING_API_KEY` assumed present). Semantically similar text does NOT produce similar vectors with it, so **vector-only** recall/precision/hit-rate numbers measure nothing about real embedding quality; `retrieval.py` marks them `passed=None` (informational, never a pass/fail gate) rather than either hiding them or misleadingly failing on a documented, expected limitation. On the intentionally small 4-file benchmark repository, `k=5` happens to cover nearly the whole corpus, so vector-only *recall*@5 is often saturated at 1.0 even though the embeddings are meaningless — but vector-only *precision*@5 (consistently lower than keyword's in an actual run) still reveals the real ranking-quality gap, because precision depends on ranking order, not just coverage. This is exactly the kind of nuance a real evaluation run should surface, not paper over. **Keyword search** (PostgreSQL full-text, no embeddings involved) and the **hybrid methods that include it** are what this suite gates pass/fail on. A real vector-retrieval number requires swapping in `OpenAIEmbeddingProvider` with a real `EMBEDDING_API_KEY` (the runner accepts an injected `embedding_provider`, so this is a one-line change for a caller who has one).

**Agent evaluation's "tool selection" is scoped to what this project actually has**, not to a capability that doesn't exist: there is no autonomous LLM-driven tool-calling loop in this codebase (Phase 8 built a `ToolRegistry` with declarative schemas, but the agent graph routes deterministically via `classify_task`, not an LLM choosing which tool to invoke). What `runners/agent.py` evaluates instead is whether that deterministic routing invokes only the capabilities appropriate to a task type, using the same `execution_log` entries the agent already produces for observability (e.g. an "answer" question must never produce a "Proposed a change" log entry) — a real, honestly-scoped stand-in, not a claim that autonomous tool selection exists.

**RAG evaluation is fully deterministic, computed before any LLM is involved:** whether the expected evidence files were actually retrieved, whether the assembled context (`rag.context.build_context`, real code, not reimplemented) literally contains expected substrings, and whether citations (`sources`) are grounded in what was actually retrieved (never referencing a file that wasn't shown). `answer_is_non_empty` is the only thing checked about `StubLLMProvider`'s generated text itself, deliberately not more — whether free text is *complete* or *fluent* is not a property this framework claims to measure deterministically. The optional LLM-judge metric (`llm_judge_score`) can score that, but requires a real `LLM_API_KEY`, is never run by default, and is always reported as informational.

**Modification and testing-loop evaluation reuse Phase 9/10's own real mechanisms** — `ModificationService`, `AgentService`, the real `apply_change`/`run_tests_after_apply` graph nodes — rather than simulating them: approval-required, stale-change-refused, single-file-scope, and "a failing change is reported as a failure, never silently accepted" are all checked against real writes to real temporary files, with one case running a real Docker container (skipped honestly if Docker isn't reachable). The testing-loop cases use `ScriptedTestRunner` (see above) to isolate the bounded-iteration/wall-clock/approval-per-retry guarantees from sandbox/LLM variability — real end-to-end Docker execution of that same loop is demonstrated separately in `backend/scripts/manual_agent_demo.py`'s scenario 4.

**What's genuinely tested locally vs. what requires a real LLM:** all 17 evaluation cases pass against real PostgreSQL, real Neo4j, and real Docker in this environment (`.venv/bin/python backend/scripts/run_evaluation.py`), and the evaluation CODE ITSELF (metric math, dataset structure, report rendering, and — importantly — each runner's ability to correctly detect a deliberately-wrong expectation as a failure, not only ever report success) is covered by 58 tests in `tests/evaluation/`. The one metric that requires a real `LLM_API_KEY` (`llm_judge_score`) is opt-in via `--llm-judge` and was not exercised live in this environment (no key present) — its response-parsing logic is unit-tested with a fake provider instead.

Explicitly out of scope for Phase 12: any invented or hard-coded performance number (every figure in this section and in a report's output comes from an actual run, never typed in); code-generation *correctness* evaluation beyond "did the modification/testing-loop mechanism behave safely" (real code quality still requires `LLM_API_KEY`, the same caveat as every LLM-dependent phase); and a large benchmark corpus (the dataset is deliberately small enough to read in full — see `dataset.py` — per this phase's own instruction that it be "transparent enough that an interviewer can inspect it").

**Running it:**
```bash
set -a; source .env; set +a
.venv/bin/python -m pytest tests/evaluation                       # 58 tests; DB/Neo4j/Docker-backed ones skip cleanly if unreachable
.venv/bin/python backend/scripts/run_evaluation.py                 # prints the full report; exit code reflects pass/fail
.venv/bin/python backend/scripts/run_evaluation.py --json out.json # also export the raw results as JSON
.venv/bin/python backend/scripts/run_evaluation.py --llm-judge      # additionally scores RAG answers with a real LLM (requires LLM_API_KEY)
```

### Observability
**Status: IMPLEMENTED (Phase 13, LOCAL TESTED including real end-to-end traces through the real agent; optional LangSmith exporter mechanism-tested with a mocked client only)**

Located at `backend/observability/`. A real tracing layer wired directly into the existing Phase 7/9/10 agent execution — not a disconnected demo — so one agent run (`AgentService.run()` through every `resume()`: human approval, fix-loop retries) can be inspected end-to-end as a single correlated trace.

**Why this exists:** before this phase, the only visibility into an agent run was `execution_log` — a flat list of safe summary strings with no timing, no hierarchy, no correlation across retrieval/graph/LLM/sandbox, and no way to inspect a *specific past* run after the fact. This phase adds structure (spans, parent/child nesting, durations, per-step status) and persistence (a local trace file per run), without changing what `execution_log` itself reports or how any existing phase behaves.

**Trace architecture**, one Trace per agent thread, spans nested exactly along this project's real execution flow:

```
Trace (trace_id == thread_id — see below)
  agent_run (kind=agent)              <- one per AgentService.run()/resume() call
    task_analyzer (node)
    planner (node)
    repository_search (retrieval)      <- vector + keyword search counts
    graph_search (graph)               <- graph candidate count
    code_analyzer (node)               <- retry decision
    decision (node)
    answer (node)
      llm_call (llm)                   <- nested: the actual LLM invocation
    -- or, for a "modify" request --
    propose_change (modification)
      llm_call (llm)                   <- ChangeGenerator's LLM call, nested here too
    human_approval (approval)          <- "interrupted" status while paused, "ok" once resumed
    apply_change (modification)
    run_tests_after_apply (sandbox)
      sandbox_run (sandbox)            <- nested: the real (or scripted) test runner call
    [fix-loop retry: propose_change -> human_approval -> apply_change -> run_tests_after_apply again]
  agent_resume (approval)              <- one per resume() call; same trace, new top-level span
    ...
```

- `models.py` — `Trace`/`Span`/`SpanEvent`, plain mutable dataclasses (built up incrementally, unlike this project's usual frozen dataclasses — the same rationale as `ingestion.models.RepositoryMetadata`).
- `tracer.py` — `Tracer`: `start_trace`/`finish_trace`/`span()` (a context manager)/`record_event`. Correlation is via `contextvars`, not a value threaded through every function signature — this is what lets a wrapped `LLMProvider` or `ToolRegistry`, neither of which has access to the current agent state, still attach its span to the right place in the trace.
- `redaction.py` — `sanitize_metadata`/`redact_text`, applied at the LAST possible moment (right before a span/trace reaches a recorder, exporter, or log line) — see "Security/redaction" below.
- `recorder.py` — `Recorder` abstraction with three implementations, ALL LOCAL, no external service ever required: `NullRecorder` (discards everything — the default), `InMemoryRecorder` (Python list, used by tests), `JSONFileRecorder` (one JSON file per trace under `.observability/traces/`, gitignored — what `inspect_trace.py` reads).
- `providers.py` — `TracedLLMProvider`/`TracedTestRunner`: pure decorators implementing the EXACT SAME `LLMProvider`/`TestRunner` interfaces they wrap, so `ModificationService`, `RAGService`, and every agent node keep calling `.generate()`/`.run()` exactly as before, with zero awareness observability exists.
- `node_tracing.py` — `traced_node`, a generic decorator applied entirely from `agent/graph.py` (the wiring layer) to each LangGraph node — `agent/nodes.py`'s own function bodies are untouched (a true zero-diff on that file). Each node's `extract` callback (also defined in `graph.py`) pulls a small, already-safe subset of that node's own return dict into span attributes.
- `logging_config.py` — `configure_json_logging()`, opt-in structured JSON logging (one object per line) for production log aggregation. `Tracer` already logs through the standard `logging` module for every span/event; installing this formatter makes all of that JSON for free.
- `report.py` — `render_trace`, the indented span-tree text renderer `inspect_trace.py` prints.
- `exporters/` — `TraceExporter` (one-method abstraction) plus the optional `LangSmithExporter` — see "External exporter" below.

**Every agent run has a unique trace ID — reusing an ID this project already requires, not inventing a second one.** `trace_id` is always the same value as the `thread_id` a caller already passes to `AgentService.run()`/`.resume()` (required since Phase 7, for LangGraph's own checkpointing). One trace spans the WHOLE thread's lifetime — every `resume()` call (a human approval, a fix-loop retry) adds to the SAME trace rather than starting a new one, closing only when the thread reaches a real terminal state (`completed`/`failed`); an `awaiting_approval` trace is flushed to its recorder but stays open for the next `resume()`.

**What's captured** (see the flow diagram above for exactly which span holds what): run/trace ID, ISO timestamps, duration per span (`time.monotonic()`-measured, immune to wall-clock adjustments), node/step name and kind, per-step status (`ok`/`error`/`interrupted`/`running`), tool name and argument KEYS (never values — see below) when `ToolRegistry` is traced, retrieval operation counts (vector/keyword/graph hit counts, ranked-candidate counts), LLM provider CLASS NAME and prompt/response LENGTH, sandbox test outcome (passed/exit code/timed-out/duration), retry iteration (`fix_iteration`, `retry_count`), and human-approval state (`approved`, and whether a span was interrupted awaiting one).

**What's deliberately NOT captured, and why:**
- **Token usage** — the current `LLMProvider` interface (`rag/llm/base.py`, unchanged by this phase per "do not rewrite completed phases") returns a plain string from `generate()`, not a usage object. There is nothing real to report, so nothing is reported — no fabricated token counts. Extending the interface to expose real usage (e.g. from Anthropic's `response.usage`) is a reasonable future addition, not implemented here since it would mean changing a completed phase's public interface for this phase's convenience alone.
- **Prompts, responses, diffs, file contents, stdout/stderr** — only LENGTHS and outcome booleans are recorded (`prompt_length`, `response_length`, `test_passed`, ...), never the text itself. This is what "do not log the user's entire source code unnecessarily" and "never expose chain-of-thought" mean in practice here: an LLM's raw output is exactly as hidden from a trace as it always was from `execution_log`.
- **Raw chain-of-thought** — this project's LLM calls (`StubLLMProvider`/`AnthropicLLMProvider`) never expose intermediate reasoning tokens in the first place; there is nothing to capture even if this phase wanted to.
- **Tool argument VALUES** — `ToolRegistry`'s optional tracing records `argument_keys` (e.g. `["relative_path"]`), never the values, since a future tool's arguments could plausibly include content that shouldn't be persisted.

**Security / redaction (`redaction.py`), defense in depth, applied before data reaches a recorder, exporter, OR log line:**
1. **Key-based**: any attribute whose key name has a whole TOKEN (split on `_`/`-`/camelCase boundaries — never a raw substring) matching `token`/`key`/`secret`/`password`/`authorization`/`credential`/... has its value replaced with `***REDACTED***`, regardless of the value. Whole-token matching is load-bearing here, not a nicety: an earlier version of this check did raw substring matching and redacted `keyword_hit_count` purely because "key" is a substring of "keyword" — a real bug, found by inspecting an actual trace and fixed with a regression test (`tests/observability/test_redaction.py`).
2. **Value-based**: even under an innocuous key, a string matching a known secret SHAPE (a GitHub token prefix, a `Bearer ...` header, an `sk-...`-style API key, a Postgres URL with an embedded password) is redacted too.
3. **Length-bounded**: every string is truncated (500 chars), every list is capped (20 items) — this is the concrete mechanism behind "do not log the user's entire source code unnecessarily."
4. Sanitization never raises: anything it can't handle becomes a safe placeholder, never an exception — a redaction bug must not be able to crash the agent either.

**Fail-safe by construction, proven, not just asserted:** every `Tracer` method catches and logs (never raises) any exception from its OWN bookkeeping. `tests/observability/test_agent_integration.py::test_a_broken_recorder_does_not_break_a_real_agent_run` constructs a `Recorder` whose every method raises, wires it into a REAL `AgentService`, and proves a real end-to-end run (real Postgres retrieval, real Neo4j graph search, a real LLM-provider call, a real routing decision) completes exactly as if no tracer were involved. Separately, LangGraph's own `interrupt()` mechanism (`GraphInterrupt`, a subclass of `GraphBubbleUp`) is explicitly NOT treated as an error — a span that observes it is marked `interrupted`, not `error`, and the exception is re-raised untouched so LangGraph's own pause/resume machinery is never disturbed.

**Backward compatibility, proven, not just asserted:** `tracer` is an optional parameter everywhere it was added (`AgentService`, `build_agent_graph`, `ToolRegistry`, `build_default_registry`), defaulting to a `Tracer(NullRecorder())` — every existing call site and test from Phases 1-12 keeps working completely unchanged. `test_agent_behaves_identically_with_and_without_a_tracer` proves the same question against the same repository produces an identical `status`/`final_response`/`execution_log` whether or not a tracer is supplied.

**How to inspect a trace locally** — no external service, no credentials, ever required for this path:
```bash
.venv/bin/python backend/scripts/inspect_trace.py --list                 # every trace_id recorded so far
.venv/bin/python backend/scripts/inspect_trace.py <trace_id>              # rendered, indented span tree
.venv/bin/python backend/scripts/inspect_trace.py <trace_id> --json       # the raw trace as JSON
```
`backend/scripts/manual_agent_demo.py` now constructs a real `Tracer(JSONFileRecorder())` and prints the `thread_id`/`trace_id` for each scenario it runs, so running that script and then `inspect_trace.py` on the ID it prints is a complete, self-contained walkthrough.

**Connected to Phase 12 evaluation, additively — the evaluation framework's control flow, metrics, and case definitions are unchanged:** `evaluation.models.CaseResult` gained one new optional field, `trace_id`. The `agent`, `modification`, and `testing_loop` evaluation runners now default to a real `Tracer(JSONFileRecorder())` (rather than the no-op default) and record each case's `thread_id` onto its result — a failing evaluation case's exact agent execution can be inspected with `inspect_trace.py <trace_id>`, and `run_evaluation.py`'s report prints that command directly under any case that ran the real agent.

**External exporter — evaluated, not assumed, per this phase's own instruction not to add a provider merely because it's popular:** this project's agent is already built on LangGraph, and LangSmith has first-class LangGraph tracing support; the `langsmith` package was already an installed transitive dependency (via `langchain-core`) before this phase touched anything, making it the lower-cost, better-fit option over Langfuse (which would need an entirely new dependency for no additional benefit to this stack). That said, this is a single-developer local project: the actual, load-bearing requirement — inspecting one run end-to-end — is already fully met by `JSONFileRecorder` + `inspect_trace.py`, with no hosted account, network call, or team-collaboration need. So `exporters/langsmith_exporter.py::LangSmithExporter` exists as a genuine, working, OPTIONAL adapter (isolated behind the one-method `TraceExporter` interface, opted into via `Tracer(recorder, exporters=[LangSmithExporter()])`) — **not wired in by default anywhere in this codebase, and NOT exercised against a live LangSmith account in this environment** (no `LANGSMITH_API_KEY` is configured, and this project's own instructions say never to request one). Its request-shaping logic is unit-tested against a mocked `langsmith.Client` (`tests/observability/test_langsmith_exporter.py`) — the same "mechanism proven, live behavior requires real credentials" pattern already used for `OpenAIEmbeddingProvider`/`AnthropicLLMProvider` elsewhere in this project. A passing test there is not proof that a live LangSmith project receives these traces.

**What's genuinely tested locally:** 78 tests (75 in `tests/observability/`, 3 added to `tests/tools/test_registry.py`) — pure unit tests for redaction (including the substring-matching regression), the three recorders, the core `Tracer` (span lifecycle, nesting, interrupt handling, fail-safety with a deliberately-broken recorder/exporter), the provider/node-tracing decorators, structured JSON logging, the trace renderer, and the mocked LangSmith exporter — plus 4 real, end-to-end integration tests through the actual `AgentService`/LangGraph workflow against real PostgreSQL and Neo4j.

Explicitly out of scope for Phase 13: token usage (see above — the interface doesn't expose it), a hosted/team-shared trace UI (the local file-based recorder + CLI inspector fully meets this project's actual, current need), and wiring `LangSmithExporter` in by default (opt-in only, and unverified against a live account).

### Security
**Status: PLANNED**

Repository content is treated as untrusted data, never as instructions. No arbitrary code execution on the host. No exposed secrets. No destructive operations without explicit approval.

## Currently Implemented

- Project directory structure (`backend/`, `frontend/`, `docs/`, `tests/`)
- Project documentation (`README.md`, this file)
- `.env.example` with placeholder configuration values
- `.gitignore` covering environment files, dependency directories, and local artifacts
- **Phase 1: Repository ingestion** (`backend/ingestion/`) — see the "Repository Ingestion" section above. Covered by an automated test suite in `tests/ingestion/` (49 tests) and a manual demonstration script at `backend/scripts/manual_ingestion_demo.py`.
- **Phase 2: Code parsing** (`backend/parsing/`) — see the "Code Parsing" section above. Covered by an automated test suite in `tests/parsing/` (27 tests) and a manual demonstration script at `backend/scripts/manual_parsing_demo.py`.
- **Phase 3: Vector search foundation** (`backend/vectorstore/`) — see the "Vector Search Foundation" section above. Covered by an automated test suite in `tests/vectorstore/` (18 tests, run against a real local PostgreSQL + pgvector instance) and a manual demonstration script at `backend/scripts/manual_vectorstore_demo.py`. Real semantic embeddings (`OpenAIEmbeddingProvider`) require `EMBEDDING_API_KEY`, which is not present in this environment — that code path is unit-tested with a mocked client, not a live API call.
- **Phase 4: Code RAG** (`backend/rag/`) — see the "Retrieval Layer (Code RAG)" section above. Covered by an automated test suite in `tests/rag/` (21 tests, run against real PostgreSQL) and a manual demonstration script at `backend/scripts/manual_rag_demo.py`. Real LLM answers (`AnthropicLLMProvider`) require `LLM_API_KEY`, which is not present in this environment — that code path is unit-tested with a mocked client, not a live API call.
- **Phase 5: Knowledge graph** (`backend/graph/`) — see the "Knowledge Graph" section above. Covered by an automated test suite in `tests/graph/` (28 tests, run against a real local Neo4j instance) and a manual demonstration script at `backend/scripts/manual_graph_demo.py`. No credential gap here — Neo4j runs locally and every test exercises it for real.
- **Phase 6: Hybrid retrieval** (`backend/hybrid/`) — see the "Hybrid Retrieval" section above. Covered by an automated test suite in `tests/hybrid/` (12 tests, run against real PostgreSQL and Neo4j together) and a manual demonstration script at `backend/scripts/manual_hybrid_demo.py`.
- **Phase 7: Stateful agent** (`backend/agent/`) — see the "Agent Layer" section above. Covered by an automated test suite in `tests/agent/test_classification.py` + `test_service.py` (19 tests, run against real PostgreSQL and Neo4j together, including the full human-approval interrupt/resume cycle) and a manual demonstration script at `backend/scripts/manual_agent_demo.py`. Phase 10 below adds 4 more tests (`test_testing_loop.py`) for the fix-loop behavior added to this same graph.
- **Phase 8: Repository tools** (`backend/tools/`) — see the "Tools" section above. Covered by an automated test suite in `tests/tools/` (30 tests: unit tests for security/file-tools/registry plus integration tests against real PostgreSQL + Neo4j) and a manual demonstration script at `backend/scripts/manual_tools_demo.py`.
- **Phase 9: Code modification** (`backend/modification/`) — see the "Code Modification" section above. Covered by an automated test suite in `tests/modification/` (7 tests, real PostgreSQL, isolated `tmp_path` fixtures) plus 3 updated agent tests proving the write actually happens on approval. Manual demonstration scripts at `backend/scripts/manual_modification_demo.py` (standalone) and `backend/scripts/manual_agent_demo.py` (wired into the full agent). Real code-change quality requires `LLM_API_KEY`, not present in this environment.
- **Phase 10: Sandbox + testing loop** (`backend/sandbox/`) — see the "Sandbox" and "Testing Loop" sections above. Covered by an automated test suite in `tests/sandbox/` (19 tests: unit tests for command detection and mocked Docker command construction, plus 6 real-Docker integration tests) and `tests/agent/test_testing_loop.py` (4 tests, real PostgreSQL + Neo4j, `FakeTestRunner`). Manual demonstration scripts at `backend/scripts/manual_sandbox_demo.py` (standalone, real Docker) and `backend/scripts/manual_agent_demo.py` (the full fix loop against real Docker, end-to-end).
- **Phase 11: GitHub integration** (`backend/github_integration/`) — see the "GitHub Integration" section above. Covered by an automated test suite in `tests/github_integration/` (54 tests: pure unit tests for URL validation and workspace confinement, mocked-`requests`/mocked-`subprocess` tests for the API client and git operations, real-network integration tests against a real public repository, a real-pipeline integration test against local PostgreSQL, and a cross-cutting test proving `GITHUB_TOKEN` never reaches the Phase 10 sandbox). Manual demonstration script at `backend/scripts/manual_github_demo.py` (real clone + real pipeline + proof that push/PR are refused without authorization). Real push/pull-request creation requires a real `GITHUB_TOKEN` and a repository the caller actually owns — not exercised against infrastructure this project doesn't control.
- **Phase 12: Evaluation** (`backend/evaluation/`) — see the "Evaluation" section above. Covered by an automated test suite in `tests/evaluation/` (58 tests: pure unit tests for metrics/models/report/dataset/scripted-runner/environment/llm-judge, plus integration tests for every evaluation runner against real PostgreSQL + Neo4j + Docker). Manual/CLI script at `backend/scripts/run_evaluation.py` — all 17 benchmark cases pass in this environment. The optional `--llm-judge` metric requires a real `LLM_API_KEY`, not present in this environment.
- **Phase 13: Observability & tracing** (`backend/observability/`) — see the "Observability" section above. Covered by an automated test suite (78 tests: 75 in `tests/observability/` plus 3 added to `tests/tools/test_registry.py`), including 4 real, end-to-end integration tests through the actual `AgentService`/LangGraph workflow against real PostgreSQL and Neo4j — one of which proves a deliberately-broken recorder never breaks a real agent run. CLI inspector at `backend/scripts/inspect_trace.py`; `backend/scripts/manual_agent_demo.py` now records and prints real, inspectable trace IDs. The optional `LangSmithExporter` is mechanism-tested with a mocked client only — not exercised against a live LangSmith account, since none is configured in this environment.

Everything else in this document remains PLANNED.
