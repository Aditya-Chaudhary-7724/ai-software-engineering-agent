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
    # F6: the binding a decision must present to `AgentService.resume` —
    # set only while awaiting approval. `interrupt_id` is LangGraph's own id
    # for the pending interrupt (a fix-loop retry gets a new one), and
    # `diff_sha256` is the hash of the exact diff in `interrupt_message`.
    interrupt_id: Optional[str] = None
    diff_sha256: Optional[str] = None
    # F6: outcome flags read from the final state (None when not reached),
    # so a caller can tell "rejected" from "applied" without parsing text.
    task_type: Optional[str] = None
    approved: Optional[bool] = None
    applied: Optional[bool] = None
    test_ran: bool = False
    test_passed: Optional[bool] = None

    def approval_binding(self) -> dict:
        """Keyword arguments binding a decision to THIS pending approval:
        `service.resume(thread_id, approved=..., **result.approval_binding())`."""
        return {"interrupt_id": self.interrupt_id, "diff_sha256": self.diff_sha256}
