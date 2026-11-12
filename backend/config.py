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
    SettingSpec("GROQ_API_KEY", False, "Groq API key — required only when LLM_PROVIDER=groq (free tier; no card)."),
    SettingSpec("EMBEDDING_API_KEY", False, "OpenAI API key (Phase 3) — real semantic embeddings; DeterministicLocalEmbeddingProvider is used without it."),
    SettingSpec("GITHUB_TOKEN", False, "GitHub PAT (Phase 11) — required only for private repos, pushing, or opening PRs; public read-only access works without it."),
    SettingSpec("API_SERVICE_TOKEN", False, "Bearer token the frontend server uses to call /api/v1 (min 32 chars) — /api/v1 refuses all requests (503) without it; health endpoints are unaffected. Server-only; never exposed to a browser."),
    SettingSpec("WORKSPACE_ROOT", False, "Absolute path of a DEDICATED directory where GitHub repositories are cloned for ingestion (frontend phase F3) — ingestion over HTTP is disabled without it."),
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


# --- LLM provider selection -------------------------------------------------
#
# LLM_PROVIDER picks the provider explicitly: "anthropic", "groq" or "stub".
# Unset keeps the original behaviour: Anthropic when LLM_API_KEY is set,
# otherwise the StubLLMProvider. An unknown value, or a provider missing a
# setting it needs, is a configuration PROBLEM: it is reported by
# check_production_config.py (exit 1) and refused at call time, never
# silently replaced by the stub.
#
# Groq: GROQ_API_KEY and LLM_MODEL are required. There is deliberately no
# default model id: model ids and limits must be taken from the Groq
# console, not hardcoded from third-party pages.
LLM_PROVIDERS = ("anthropic", "groq", "stub")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_LLM_MAX_COMPLETION_TOKENS = 2048
MAX_LLM_MAX_COMPLETION_TOKENS = 32768


def llm_provider_name() -> str:
    """The provider that WILL be used (not validated; see
    llm_configuration_problems). Not a secret."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return "anthropic" if os.environ.get("LLM_API_KEY") else "stub"


def llm_max_completion_tokens() -> int:
    raw = os.environ.get("LLM_MAX_COMPLETION_TOKENS", "").strip()
    return int(raw) if raw else DEFAULT_LLM_MAX_COMPLETION_TOKENS


def llm_configuration_problems() -> List[str]:
    """Human-readable problems with the LLM configuration. Names settings,
    never their values."""
    name = llm_provider_name()
    if name not in LLM_PROVIDERS:
        return [f"LLM_PROVIDER must be one of {', '.join(LLM_PROVIDERS)}"]
    problems: List[str] = []
    if name == "anthropic" and not os.environ.get("LLM_API_KEY"):
        problems.append("LLM_PROVIDER=anthropic requires LLM_API_KEY")
    if name == "groq":
        if not os.environ.get("GROQ_API_KEY"):
            problems.append("LLM_PROVIDER=groq requires GROQ_API_KEY")
        if not os.environ.get("LLM_MODEL", "").strip():
            problems.append("LLM_PROVIDER=groq requires LLM_MODEL (copy the model id from the Groq console)")
        raw = os.environ.get("LLM_MAX_COMPLETION_TOKENS", "").strip()
        if raw and not (raw.isdigit() and 1 <= int(raw) <= MAX_LLM_MAX_COMPLETION_TOKENS):
            problems.append(f"LLM_MAX_COMPLETION_TOKENS must be an integer from 1 to {MAX_LLM_MAX_COMPLETION_TOKENS}")
    return problems


# Feature flags: NOT secrets, so (unlike the settings above) their
# effective value may be reported. Each is fail-closed: only the exact
# value "true" (case-insensitive) turns the feature on.
#
# SANDBOX_ENABLED: whether the API's agent worker may run repository tests
# in the Docker sandbox (Phase 10). Default OFF. A hosted deployment must
# leave it off: the API image deliberately has no Docker CLI, and the API
# container must never be given the Docker socket (docs/deployment.md
# section 7). When off, agent tasks still propose/apply approved changes
# but report "tests not run: sandbox disabled in this deployment", never
# a verified result. Developer scripts that construct DockerTestRunner
# directly (backend/scripts/manual_*.py, evaluation) are not affected.
SANDBOX_ENABLED_ENV_VAR = "SANDBOX_ENABLED"


@dataclass(frozen=True)
class FeatureFlagStatus:
    name: str
    enabled: bool
    description: str


def sandbox_enabled() -> bool:
    return os.environ.get(SANDBOX_ENABLED_ENV_VAR, "").strip().lower() == "true"


def feature_flags() -> List[FeatureFlagStatus]:
    return [
        FeatureFlagStatus(
            SANDBOX_ENABLED_ENV_VAR,
            sandbox_enabled(),
            "Run agent-task tests in the Docker sandbox (default off; keep off in hosted deployments).",
        ),
    ]
