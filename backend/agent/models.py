from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AgentRunResult:
    status: str  # "completed" | "awaiting_approval"
    question: str
    final_response: Optional[str]
    sources: list = field(default_factory=list)
    execution_log: list = field(default_factory=list)
    interrupt_message: Optional[dict] = None
