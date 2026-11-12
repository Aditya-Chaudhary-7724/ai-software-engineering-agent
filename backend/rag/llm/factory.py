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


def configured_llm_provider() -> Tuple[LLMProvider, str]:
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
                model=os.environ["LLM_MODEL"].strip(),
                max_completion_tokens=config.llm_max_completion_tokens(),
                reasoning_effort=os.environ.get("LLM_REASONING_EFFORT", "").strip() or None,
            ),
            "groq",
        )
    return StubLLMProvider(), "stub"
