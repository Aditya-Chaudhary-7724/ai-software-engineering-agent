"""Safe code modification (Phase 9).

instruction -> find affected file -> generate proposed content ->
unified diff -> (human approval, wired in agent/graph.py) -> apply.

`apply_change` is the only function anywhere in this project that
writes to a repository file. It is never called except after the real
human-approval interrupt Phase 7 built, and it refuses to apply a
proposal if the target file changed since the proposal was generated.

Content quality requires a real LLM (AnthropicLLMProvider +
LLM_API_KEY) — with the stub provider, the pipeline mechanism is fully
real and tested, but the "proposed content" is a fixed placeholder,
not valid code. Same caveat as every other LLM-dependent phase.
"""

from modification.diff import generate_unified_diff
from modification.exceptions import ModificationError, StaleChangeError
from modification.models import ModificationResult, ProposedChange
from modification.service import ModificationService

__all__ = [
    "ModificationError",
    "StaleChangeError",
    "ProposedChange",
    "ModificationResult",
    "generate_unified_diff",
    "ModificationService",
]
