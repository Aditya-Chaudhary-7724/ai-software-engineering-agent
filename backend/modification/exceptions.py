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


class ProtectedFileError(ModificationError):
    """F7 hardening: the target is a sensitive file (`.env`, `*.pem`, ...)
    or lives in an ignored directory (`.git`, `node_modules`, ...). The
    agent never selects such a file (they are never indexed), but the
    file-writing boundary must not rely on that: a write under `.git/hooks`
    would be code execution on the host the next time someone runs git.
    """


class ChangeIntegrityError(ModificationError):
    """F7 hardening: the proposed content does not reproduce the diff that
    was shown for approval. F6 binds a human approval to the diff's hash,
    but `apply_change` writes `proposed_content`; this check makes the
    approved diff and the written bytes provably the same change.
    """
