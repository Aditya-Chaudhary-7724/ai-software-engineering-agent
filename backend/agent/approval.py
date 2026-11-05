"""Human-approval binding (frontend phase F6).

An approval is only meaningful if it is bound to WHAT was approved. Before
F6, `human_approval_node` accepted `bool(decision)` — so `"no"`, `1` or
`[0]` all counted as approval, and nothing tied a decision to the diff the
reviewer actually saw. Now a decision is valid only when:

1. `approved` is the JSON/Python boolean `True` itself (`is True`, never
   truthiness), and
2. it carries the SHA-256 of the exact pending diff, compared in constant
   time against a hash the SERVER recomputes from its own checkpointed
   state — never against a hash the caller merely echoes back, and
3. (enforced by `AgentService.resume`) it names the id of the interrupt
   that is pending right now, so a decision for an earlier proposal (e.g.
   before a fix-loop retry) cannot be replayed against a later one.

Anything else fails closed: it is treated as a rejection, never approval.
"""

import hashlib
import hmac
from typing import Any, Optional

_SHA256_HEX_LENGTH = 64


class ApprovalError(Exception):
    """Base class: a decision was refused before it reached the graph.
    The pending approval is left untouched, so a valid decision can still
    be made."""

    code = "approval_invalid"


class InvalidApprovalError(ApprovalError):
    """`approved` was not a real boolean, or the binding fields were malformed."""

    code = "approval_invalid"


class NoPendingApprovalError(ApprovalError):
    """The thread is not paused at a human-approval interrupt (never was,
    already decided, or finished)."""

    code = "approval_not_pending"


class StaleApprovalError(ApprovalError):
    """The decision names an interrupt that is no longer the pending one."""

    code = "approval_stale"


class ApprovalMismatchError(ApprovalError):
    """The decision's diff hash does not match the pending diff."""

    code = "approval_diff_mismatch"


class ApprovalInProgressError(ApprovalError):
    """Another decision for the same thread is being applied right now."""

    code = "approval_in_progress"


def diff_sha256(diff: Optional[str]) -> str:
    return hashlib.sha256((diff or "").encode("utf-8")).hexdigest()


def is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(c in "0123456789abcdef" for c in value)
    )


def hashes_match(presented: Any, expected: str) -> bool:
    if not is_sha256_hex(presented):
        return False
    return hmac.compare_digest(presented.encode("ascii"), expected.encode("ascii"))


def is_bound_approval(decision: Any, expected_diff_sha256: str) -> bool:
    """True only for `{"approved": True, "diff_sha256": <matching hash>}`.

    Used by `human_approval_node` as the last line of defense: even a
    caller that drives the LangGraph graph directly (bypassing
    `AgentService.resume`'s checks) cannot get a change applied with
    `Command(resume=True)`, `"yes"`, `1`, or a hash for some other diff.
    """
    if not isinstance(decision, dict):
        return False
    return decision.get("approved") is True and hashes_match(decision.get("diff_sha256"), expected_diff_sha256)
