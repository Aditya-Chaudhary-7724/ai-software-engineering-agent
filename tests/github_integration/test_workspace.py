"""Pure unit tests — no network, no filesystem outside tmp_path."""

import pytest

from github_integration.exceptions import WorkspaceError
from github_integration.models import RepositoryReference
from github_integration.workspace import resolve_clone_destination


def test_resolves_owner_repo_under_workspace_root(tmp_path):
    reference = RepositoryReference(owner="octocat", repo="Hello-World", url="https://github.com/octocat/Hello-World")

    destination = resolve_clone_destination(str(tmp_path), reference)

    assert destination == (tmp_path / "octocat" / "Hello-World").resolve()


def test_creates_the_workspace_root_if_missing(tmp_path):
    workspace_root = tmp_path / "does" / "not" / "exist" / "yet"
    reference = RepositoryReference(owner="octocat", repo="Hello-World", url="https://github.com/octocat/Hello-World")

    resolve_clone_destination(str(workspace_root), reference)

    assert workspace_root.is_dir()


def test_rejects_a_traversal_attempt_even_if_it_bypassed_url_validation(tmp_path):
    """Defense in depth: url_validation.py's regex already rejects `..`
    in owner/repo, but this proves the SECOND, independent layer
    (reused from Phase 8's resolve_safe_path) also refuses it on its
    own, even when called directly with an already-malicious reference.
    """
    reference = RepositoryReference(owner="..", repo="..", url="https://github.com/../..")

    with pytest.raises(WorkspaceError):
        resolve_clone_destination(str(tmp_path), reference)


def test_rejects_an_absolute_path_style_escape(tmp_path):
    reference = RepositoryReference(owner="octocat", repo="../../../etc", url="https://github.com/octocat/etc")

    with pytest.raises(WorkspaceError):
        resolve_clone_destination(str(tmp_path), reference)
