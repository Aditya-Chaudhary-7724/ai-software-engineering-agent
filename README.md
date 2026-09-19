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
2. Code parsing
3. Vector search
4. Code RAG
5. Neo4j knowledge graph
6. Hybrid retrieval
7. LangGraph agent
8. Repository tools
9. Code modification
10. Testing loop
11. GitHub integration
12. Evaluation
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

Project setup completed. **Phase 1 (repository ingestion) is implemented.**

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
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest                              # run the test suite
.venv/bin/python backend/scripts/manual_ingestion_demo.py  # manual demonstration
```

See `docs/architecture.md` for the full Phase 1 design and `docs/architecture.md`'s IMPLEMENTED/PLANNED breakdown for what remains.
