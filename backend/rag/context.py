"""Assembles a bounded context string from ranked candidates.

Candidates are already small (one function/class each, from Phase 2's
line ranges), but a repository-level question can still surface many
of them — `max_chars` bounds the total size actually sent to the LLM,
which is what "do not blindly dump entire files into the LLM" means in
practice here.
"""

from typing import Any, Sequence

DEFAULT_MAX_CONTEXT_CHARS = 8000


def build_context(
    candidates: Sequence[Any], max_chars: int = DEFAULT_MAX_CONTEXT_CHARS
) -> tuple[str, list[Any]]:
    """Returns (context_text, included_candidates).

    Deliberately untyped by element: this operates on any object with
    `.relative_path: str`, `.start_line: int`, `.end_line: int`, and
    `.content: str` — both rag.models.Candidate and
    hybrid.models.HybridCandidate satisfy this, and are passed here
    directly without conversion.

    `included_candidates` is exactly the subset whose content made it
    into `context_text`, in order — this is what Source citations must
    be built from, not the full candidate list, so citations never
    reference something the LLM wasn't actually shown.
    """
    sections: list[str] = []
    included: list[Any] = []
    total_chars = 0

    for candidate in candidates:
        header = f"# {candidate.relative_path}:{candidate.start_line}-{candidate.end_line}"
        section = f"{header}\n{candidate.content}"

        if total_chars + len(section) > max_chars and included:
            break

        sections.append(section)
        included.append(candidate)
        total_chars += len(section)

    return "\n\n".join(sections), included
