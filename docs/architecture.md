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

### AI / Agent Layer
**Status: PLANNED**

Coordinates retrieval, reasoning, and tool use. Will be implemented as a stateful workflow (see Agent Layer below), not a single autonomous loop.

### Retrieval Layer (Code RAG)
**Status: PLANNED**

Chunking of source code, embedding generation, and vector storage/retrieval using PostgreSQL + pgvector. Enables semantic search over the codebase.

### Knowledge Graph
**Status: PLANNED**

A Neo4j-backed graph representing structural relationships in code: functions, classes, modules, imports, callers, and callees. Enables reasoning about dependencies that plain vector search cannot capture.

### Hybrid Retrieval
**Status: PLANNED**

Combines vector-based semantic retrieval with graph-based structural retrieval to answer questions that require both meaning and structure.

### Agent Layer
**Status: PLANNED**

A stateful LangGraph workflow with explicit state and conditional routing:

```
Task Analyzer
  -> Planner
  -> Repository Search
  -> Graph Search
  -> Code Analyzer
  -> Decision
  -> Answer / Modify / Test / Human Approval
  -> Final Response
```

Design constraints for this layer:
- Explicit state, not implicit conversation history
- Conditional routing based on task type
- Bounded retries to prevent infinite loops
- Human approval required before consequential actions

### Tools
**Status: PLANNED**

Repository analysis tools the agent can call: file search, dependency inspection, code reading, and (later) code modification and test execution tools.

### Database
**Status: PLANNED**

PostgreSQL with the pgvector extension for embeddings and application data. No database has been provisioned yet.

### Sandbox
**Status: PLANNED**

An isolated execution environment for running tests and, later, executing modified code safely. Arbitrary repository code must never run directly on the host machine.

### GitHub Integration
**Status: PLANNED**

Repository ingestion from GitHub, and eventually pull request creation. All GitHub write operations will require explicit human approval; the agent will never authenticate as its own identity or add itself as a contributor.

### Evaluation
**Status: PLANNED**

Measurement of RAG quality (retrieval precision/recall, faithfulness), agent behavior (task success, tool selection, failure recovery), and code generation quality (tests passed, correctness, regression rate). Only metrics that are actually measured will be reported; no invented metrics.

### Observability
**Status: PLANNED**

Tracking of agent runs, tool calls, retrieval results, latency, model calls, token usage, and errors. The UI will show safe execution summaries (e.g. "Searching repository...") without exposing hidden chain-of-thought.

### Security
**Status: PLANNED**

Repository content is treated as untrusted data, never as instructions. No arbitrary code execution on the host. No exposed secrets. No destructive operations without explicit approval.

## Currently Implemented

- Project directory structure (`backend/`, `frontend/`, `docs/`, `tests/`)
- Project documentation (`README.md`, this file)
- `.env.example` with placeholder configuration values
- `.gitignore` covering environment files, dependency directories, and local artifacts
- **Phase 1: Repository ingestion** (`backend/ingestion/`) — see the "Repository Ingestion" section above. Covered by an automated test suite in `tests/ingestion/` (49 tests) and a manual demonstration script at `backend/scripts/manual_ingestion_demo.py`.

Everything else in this document — parsing, embeddings, vector search, the knowledge graph, hybrid retrieval, the agent layer, tools, the database, the sandbox, GitHub integration, evaluation, and observability — remains PLANNED.
