"""The single place that turns configuration into an LLM provider.

Replaces the copies in `api/routes/ask.py` and `agent_tasks/executor.py`,
which could only choose Anthropic or the stub. What is valid is defined
once, in `backend/config.py` (`llm_configuration_problems`), which
`check_production_config.py` also reports before a deploy.

Returns `(provider, name)`: the name ("anthropic" | "groq" | "stub") is
recorded with answers and tasks so the UI can label stub output honestly.
"""

import os
from typing import Tuple

import config
from rag.llm.base import LLMProvider
from rag.llm.exceptions import LLMConfigurationError
from rag.llm.stub_provider import StubLLMProvider


ROLE_OPERATOR = "operator"
ROLE_DEMO = "demo"


def configured_llm_provider(role: str = ROLE_OPERATOR) -> Tuple[LLMProvider, str]:
    """`role="demo"` (the public demo principal) uses DEMO_LLM_MODEL and
    DEMO_LLM_REASONING_EFFORT on Groq, so demo traffic draws on a different
    per-model quota than the operator's. Other providers are role-agnostic."""
    problems = config.llm_configuration_problems()
    if problems:
        raise LLMConfigurationError("; ".join(problems))
    name = config.llm_provider_name()
    if name == "anthropic":
        from rag.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(), "anthropic"
    if name == "groq":
        from rag.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

        return (
            OpenAICompatibleLLMProvider(
                api_key=os.environ["GROQ_API_KEY"],
                base_url=config.GROQ_BASE_URL,
                model=_groq_setting("LLM_MODEL", role).strip(),
                max_completion_tokens=config.llm_max_completion_tokens(),
                reasoning_effort=_groq_setting("LLM_REASONING_EFFORT", role).strip() or None,
            ),
            "groq",
        )
    return StubLLMProvider(), "stub"


def _groq_setting(name: str, role: str) -> str:
    if role == ROLE_DEMO:
        value = os.environ.get(f"DEMO_{name}", "")
        if name == "LLM_MODEL" and not value.strip():
            # Never silently fall back to the operator's model (and quota).
            raise LLMConfigurationError("demo requests require DEMO_LLM_MODEL")
        return value
    return os.environ.get(name, "")
