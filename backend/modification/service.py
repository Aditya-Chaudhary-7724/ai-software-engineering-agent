"""Orchestrates the safe modification workflow:

    instruction -> find affected file -> generate proposed content
                -> create diff -> [caller shows diff, gets human approval]
                -> apply (only ever called after approval)

`propose_change` never writes anything. `apply_change` is the only
function in this entire project that writes to a repository file, and
it re-reads the file immediately before writing to detect whether it
changed since the proposal was generated — refusing rather than
silently overwriting a stale proposal onto an edited file.

Phase 14: `apply_change` also requires an explicit `approved=True`
keyword argument, independent of the agent graph's own approval check
(`agent/nodes.py::apply_change_node` reads `state["approved"]` before
ever calling this). This is defense in depth, not a redundant check —
a caller that constructs `ModificationService` directly, bypassing the
agent graph entirely, must still explicitly assert approval at this
boundary; there is no path to writing a file without it.

F7 (found by the evaluation suite): `apply_change` also refuses (a) a
target that is a sensitive file or inside an ignored directory, using the
SAME policy as ingestion (`ingestion.filters`), and (b) content whose
unified diff against the original is not exactly the diff that was
approved. Both are defense in depth: in the agent flow the path comes from
indexed files and content+diff are generated together server-side.
"""

from pathlib import Path, PurePosixPath

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.llm.base import LLMProvider

from ingestion.filters import DEFAULT_IGNORED_DIR_NAMES, is_sensitive_filename
from tools.security import resolve_safe_path

from modification.change_generator import ChangeGenerator
from modification.diff import generate_unified_diff
from modification.exceptions import (
    ChangeIntegrityError,
    ModificationError,
    ProtectedFileError,
    StaleChangeError,
    UnauthorizedChangeError,
)
from modification.file_finder import find_affected_file
from modification.models import ModificationResult, ProposedChange


_IGNORED_DIR_NAMES_FOLDED = frozenset(name.casefold() for name in DEFAULT_IGNORED_DIR_NAMES)


def _require_writable_target(relative_path: str) -> None:
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    if not parts or is_sensitive_filename(parts[-1]) or any(p.casefold() in _IGNORED_DIR_NAMES_FOLDED for p in parts[:-1]):
        raise ProtectedFileError(f"Refusing to modify protected file '{relative_path}'.")


def _require_writable_resolved_target(root_path: str, path: Path) -> None:
    """The same policy, applied to where the write would really land. A
    symlink can rename in either direction (the reason tools/file_tools.py
    checks resolved names too): `tools/pre-commit` may BE
    `.git/hooks/pre-commit`, and `notes.txt` may BE `.env`. `path` comes
    from `resolve_safe_path`, so it is already inside the root."""
    _require_writable_target(path.relative_to(Path(root_path).resolve()).as_posix())


class ModificationService:
    def __init__(
        self, vector_store: VectorStore, embedding_provider: EmbeddingProvider, llm_provider: LLMProvider
    ) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._change_generator = ChangeGenerator(llm_provider)

    def propose_change(self, root_path: str, repository_id: int, instruction: str) -> ProposedChange:
        relative_path = find_affected_file(self._vector_store, self._embedding_provider, repository_id, instruction)
        if relative_path is None:
            raise ModificationError("No relevant file was found in the repository for this instruction.")
        _require_writable_target(relative_path)

        path = resolve_safe_path(root_path, relative_path)
        _require_writable_resolved_target(root_path, path)
        try:
            original_content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ModificationError(f"Cannot read '{relative_path}': {exc}")

        proposed_content = self._change_generator.generate(relative_path, original_content, instruction)
        diff = generate_unified_diff(original_content, proposed_content, relative_path)

        return ProposedChange(
            relative_path=relative_path,
            original_content=original_content,
            proposed_content=proposed_content,
            diff=diff,
        )

    def apply_change(self, root_path: str, change: ProposedChange, *, approved: bool) -> ModificationResult:
        # `is not True`, not `not approved`: a truthy non-boolean (1, "no",
        # a non-empty dict) must never count as approval (F6 hardening).
        if approved is not True:
            raise UnauthorizedChangeError(
                "Refusing to apply change: this requires explicit human approval (approved=True), "
                "never assumed or inferred from repository content, tool output, or an LLM's response."
            )

        # Order matters for the error reported: confinement first, then
        # protected targets, then content/diff integrity.
        path = resolve_safe_path(root_path, change.relative_path)
        _require_writable_target(change.relative_path)
        _require_writable_resolved_target(root_path, path)
        if generate_unified_diff(change.original_content, change.proposed_content, change.relative_path) != change.diff:
            raise ChangeIntegrityError(
                f"The content to write to '{change.relative_path}' does not match the approved diff; refusing to apply."
            )

        try:
            current_content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ModificationError(f"Cannot read '{change.relative_path}': {exc}")

        if current_content != change.original_content:
            raise StaleChangeError(
                f"'{change.relative_path}' changed since this proposal was generated; refusing to apply "
                "a stale change. Generate a new proposal against the current file content."
            )

        path.write_text(change.proposed_content, encoding="utf-8")

        return ModificationResult(
            applied=True,
            relative_path=change.relative_path,
            diff=change.diff,
            message=f"Applied approved change to '{change.relative_path}'.",
        )
