from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProposedChange:
    """A generated but not-yet-applied change to a single file.

    Carries `original_content` so `apply_change` can detect (and refuse)
    a stale change — the file having been edited since this proposal
    was generated.
    """

    relative_path: str
    original_content: str
    proposed_content: str
    diff: str


@dataclass(frozen=True)
class ModificationResult:
    applied: bool
    relative_path: Optional[str]
    diff: Optional[str]
    message: str
