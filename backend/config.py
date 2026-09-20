"""Phase 15: a centralized, read-only view of this project's
environment configuration.

Used by BOTH `backend/scripts/check_production_config.py` (a CLI a
human/CI runs before deploying) and `backend/api/health.py`'s
readiness check, so "what's required vs optional" is defined exactly
once rather than drifting between two hand-maintained lists.

Never logs, prints, or returns an actual secret VALUE — only whether a
variable is SET (a boolean). This is the same principle Phase 13/14's
redaction applies at the recorder/logger boundary, applied here at the
earliest possible point instead: the value never leaves `os.environ`
in the first place.
"""

import os
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SettingSpec:
    name: str
    required: bool
    description: str


@dataclass(frozen=True)
class SettingStatus:
    name: str
    required: bool
    configured: bool
    description: str


# Required for the application's core functionality: every RAG/agent
# code path depends on the vector store (Phase 3). Without it, nothing
# in this application can do useful work at all.
REQUIRED_SETTINGS: List[SettingSpec] = [
    SettingSpec("DATABASE_URL", True, "PostgreSQL + pgvector connection string (Phase 3) — required for all retrieval/agent functionality."),
]

# Optional: each unlocks a specific capability; its ABSENCE degrades
# that one capability gracefully (an honest gap, matching this
# project's own "no invented capability" principle throughout Phases
# 1-14) rather than failing application startup.
OPTIONAL_SETTINGS: List[SettingSpec] = [
    SettingSpec("NEO4J_URI", False, "Neo4j knowledge graph (Phase 5/6) — hybrid retrieval degrades to vector+keyword without it."),
    SettingSpec("NEO4J_USERNAME", False, "Neo4j username — required together with NEO4J_URI and NEO4J_PASSWORD."),
    SettingSpec("NEO4J_PASSWORD", False, "Neo4j password — required together with NEO4J_URI and NEO4J_USERNAME."),
    SettingSpec("LLM_API_KEY", False, "Anthropic API key (Phase 4) — real LLM answers/modifications; StubLLMProvider is used without it."),
    SettingSpec("EMBEDDING_API_KEY", False, "OpenAI API key (Phase 3) — real semantic embeddings; DeterministicLocalEmbeddingProvider is used without it."),
    SettingSpec("GITHUB_TOKEN", False, "GitHub PAT (Phase 11) — required only for private repos, pushing, or opening PRs; public read-only access works without it."),
    SettingSpec("LANGSMITH_API_KEY", False, "Optional LangSmith trace export (Phase 13) — local JSON tracing (JSONFileRecorder) works fully without it."),
]

ALL_SETTINGS: List[SettingSpec] = REQUIRED_SETTINGS + OPTIONAL_SETTINGS


def check_environment() -> List[SettingStatus]:
    """Returns the configuration status of every known setting, in a
    fixed, documented order. Never includes the actual value of any
    variable — only `bool(os.environ.get(name))`.
    """
    return [
        SettingStatus(name=spec.name, required=spec.required, configured=bool(os.environ.get(spec.name)), description=spec.description)
        for spec in ALL_SETTINGS
    ]


def missing_required_settings() -> List[str]:
    return [status.name for status in check_environment() if status.required and not status.configured]
