"""Exceptions raised by the code modification subsystem."""


class ModificationError(Exception):
    """Base class for all modification-related errors."""


class StaleChangeError(ModificationError):
    """Raised when apply_change detects the target file changed since the
    proposal was generated — refuse rather than silently clobber it.
    """


class UnauthorizedChangeError(ModificationError):
    """Raised when `apply_change` is called without `approved=True`.

    Phase 14 hardening: previously this invariant ("a change is never
    applied without human approval") was enforced only by the calling
    agent node (`apply_change_node` checking `state["approved"]`
    before ever calling this service) — a caller that constructed
    `ModificationService` directly and called `apply_change` bypassed
    it entirely. This service-level check is defense in depth,
    independent of the agent graph: `approved` must be an explicit,
    caller-supplied `True`, never defaulted or inferred from repository
    content, tool output, or an LLM's response.
    """
