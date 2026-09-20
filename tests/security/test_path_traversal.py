"""Filesystem hardening (Phase 14, section 4): adversarial regression
tests against `tools.security.resolve_safe_path`, the ONE path-safety
boundary this project reuses everywhere a repository-relative path
touches the filesystem (`read_file`/`analyze_code` — Phase 8,
`ModificationService` — Phase 9, `github_integration.workspace` —
Phase 11). These tests exercise the REAL function against a REAL
filesystem, including real symlinks — not mocks — since path handling
bugs are exactly the class of bug that doesn't show up with a fake
filesystem.
"""

import pytest

from tools.exceptions import ToolAuthorizationError
from tools.security import resolve_safe_path


@pytest.fixture
def repo_with_escape_targets(tmp_path):
    """A repo root plus a SEPARATE directory outside it, wired up with
    both a direct file->file symlink and a directory->directory symlink
    pointing from inside the root to outside it.
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real.py").write_text("def real(): pass\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET — should never be reachable")

    (root / "evil_file_link").symlink_to(outside / "secret.txt")
    (root / "evil_dir_link").symlink_to(outside)

    return root, outside


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../../../etc/passwd",
        "../outside/secret.txt",
        "a/../../outside/secret.txt",
        "a/b/../../../outside/secret.txt",
        "/etc/passwd",
        "/etc/../etc/passwd",
    ],
)
def test_rejects_relative_and_absolute_traversal(repo_with_escape_targets, malicious_path):
    root, _ = repo_with_escape_targets
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(root), malicious_path)


def test_rejects_a_symlink_pointing_directly_outside_the_root(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(root), "evil_file_link")


def test_rejects_a_path_through_a_symlinked_directory_escaping_the_root(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(root), "evil_dir_link/secret.txt")


def test_allows_a_legitimate_relative_path(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    resolved = resolve_safe_path(str(root), "real.py")
    assert resolved == (root / "real.py").resolve()


def test_allows_the_root_itself(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    assert resolve_safe_path(str(root), ".") == root.resolve()


def test_rejects_embedded_null_byte_rather_than_crashing_uncontrolled(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    # A null byte in a path is invalid at the OS level; this must surface
    # as SOME exception (never a silent, unsafe path), not necessarily
    # ToolAuthorizationError specifically — Python itself refuses it.
    with pytest.raises(Exception):
        resolve_safe_path(str(root), "sub\x00dir/x")


def test_a_repository_file_named_dot_env_is_still_confined_but_content_access_is_separately_gated(tmp_path):
    """resolve_safe_path's ONLY job is "stay inside the root" — a file
    named `.env` that genuinely lives inside the repository root is not
    a traversal violation. Whether it's SAFE to expose its content is a
    separate control (ingestion filtering / tools.file_tools — see
    test_sensitive_file_exclusion.py), not this function's concern.
    """
    root = tmp_path
    (root / ".env").write_text("SECRET=abc123")
    resolved = resolve_safe_path(str(root), ".env")
    assert resolved == (root / ".env").resolve()


def test_nested_traversal_through_multiple_valid_looking_segments(repo_with_escape_targets):
    root, _ = repo_with_escape_targets
    (root / "a").mkdir()
    (root / "a" / "b").mkdir()
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(root), "a/b/../../../outside/secret.txt")
