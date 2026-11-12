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
    SettingSpec("DEMO_API_SERVICE_TOKEN", False, "Second bearer token, for the public demo only (min 32 chars, must differ from API_SERVICE_TOKEN): read-only scope over DEMO_REPOSITORY_IDS, enforced by the backend. Server-only."),
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


# --- Embedding provider selection -------------------------------------------
#
# EMBEDDING_PROVIDER: "openai", "fastembed" or "deterministic_local". Unset
# keeps the original behaviour: OpenAI when EMBEDDING_API_KEY is set,
# otherwise the deterministic, NON-semantic hash stand-in. Invalid settings
# are configuration problems (reported, then refused), as for the LLM.
#
# fastembed runs an open-source model locally (ONNX Runtime, no PyTorch, no
# API calls). Only models listed here are allowed: each has a KNOWN vector
# dimension (the database stores vectors per dimension) and a PINNED
# Hugging Face revision, so the weights cannot change underneath an index.
EMBEDDING_PROVIDERS = ("openai", "fastembed", "deterministic_local")


@dataclass(frozen=True)
class LocalEmbeddingModel:
    name: str  # the model name fastembed knows it by
    dimension: int
    hf_repo: str  # where fastembed's ONNX export of it lives
    hf_revision: str  # exact commit of that repo


FASTEMBED_MODELS = {
    model.name: model
    for model in (
        LocalEmbeddingModel(
            name="BAAI/bge-small-en-v1.5",
            dimension=384,
            hf_repo="Qdrant/bge-small-en-v1.5-onnx-Q",
            hf_revision="aa8f8b060edb00e03bfdd08813a2949946c8ba55",
        ),
    )
}
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
# Batch size bounds peak memory: measured on linux/arm64 for bge-small,
# batch 32 peaked at 1346 MiB, batch 8 at 524 MiB, at about the same speed.
DEFAULT_EMBEDDING_BATCH_SIZE = 8
MAX_EMBEDDING_BATCH_SIZE = 64


def embedding_provider_name() -> str:
    explicit = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return "openai" if os.environ.get("EMBEDDING_API_KEY") else "deterministic_local"


def fastembed_model() -> str:
    return os.environ.get("EMBEDDING_MODEL", "").strip() or DEFAULT_FASTEMBED_MODEL


def _bounded_int(name: str, low: int, high: int) -> bool:
    raw = os.environ.get(name, "").strip()
    return not raw or (raw.isdigit() and low <= int(raw) <= high)


def embedding_batch_size() -> int:
    raw = os.environ.get("EMBEDDING_BATCH_SIZE", "").strip()
    return int(raw) if raw else DEFAULT_EMBEDDING_BATCH_SIZE


def embedding_configuration_problems() -> List[str]:
    name = embedding_provider_name()
    if name not in EMBEDDING_PROVIDERS:
        return [f"EMBEDDING_PROVIDER must be one of {', '.join(EMBEDDING_PROVIDERS)}"]
    problems: List[str] = []
    if name == "openai" and not os.environ.get("EMBEDDING_API_KEY"):
        problems.append("EMBEDDING_PROVIDER=openai requires EMBEDDING_API_KEY")
    if name == "fastembed":
        if fastembed_model() not in FASTEMBED_MODELS:
            problems.append(f"EMBEDDING_MODEL must be one of {', '.join(sorted(FASTEMBED_MODELS))} for fastembed")
        if not _bounded_int("EMBEDDING_BATCH_SIZE", 1, MAX_EMBEDDING_BATCH_SIZE):
            problems.append(f"EMBEDDING_BATCH_SIZE must be an integer from 1 to {MAX_EMBEDDING_BATCH_SIZE}")
        if not _bounded_int("EMBEDDING_THREADS", 1, 64):
            problems.append("EMBEDDING_THREADS must be an integer from 1 to 64")
        model_path = os.environ.get("EMBEDDING_MODEL_PATH", "").strip()
        if model_path and not os.path.isabs(model_path):
            problems.append("EMBEDDING_MODEL_PATH must be an absolute path")
    return problems


# --- Public demo -------------------------------------------------------------
#
# The demo is a second backend principal: requests bearing
# DEMO_API_SERVICE_TOKEN get a deny-by-default, read-only scope
# (api/demo_scope.py) restricted to DEMO_REPOSITORY_IDS. The operator token
# (API_SERVICE_TOKEN) keeps full power. Demo mode is OFF unless the demo token
# is set; an invalid demo token is never accepted (fail closed).
DEMO_TOKEN_MIN_LENGTH = 32


def demo_repository_ids() -> frozenset:
    """Valid ids from DEMO_REPOSITORY_IDS (comma-separated); invalid entries
    are reported by demo_configuration_problems and never allowed."""
    ids = set()
    for part in os.environ.get("DEMO_REPOSITORY_IDS", "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) >= 1:
            ids.add(int(part))
    return frozenset(ids)


def demo_mode_enabled() -> bool:
    return bool(os.environ.get("DEMO_API_SERVICE_TOKEN"))


def demo_configuration_problems() -> List[str]:
    if not demo_mode_enabled():
        return []
    problems: List[str] = []
    token = os.environ.get("DEMO_API_SERVICE_TOKEN", "")
    if len(token) < DEMO_TOKEN_MIN_LENGTH:
        problems.append(f"DEMO_API_SERVICE_TOKEN must be at least {DEMO_TOKEN_MIN_LENGTH} characters")
    if token == os.environ.get("API_SERVICE_TOKEN", ""):
        problems.append("DEMO_API_SERVICE_TOKEN must differ from API_SERVICE_TOKEN")
    raw_ids = [p.strip() for p in os.environ.get("DEMO_REPOSITORY_IDS", "").split(",") if p.strip()]
    if any(not (p.isdigit() and int(p) >= 1) for p in raw_ids):
        problems.append("DEMO_REPOSITORY_IDS must be a comma-separated list of repository ids")
    if not demo_repository_ids():
        problems.append("demo mode is enabled but DEMO_REPOSITORY_IDS lists no repository")
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
