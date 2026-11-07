"""F7 hardening of the only file-writing boundary
(`ModificationService.apply_change`): protected targets and content/diff
integrity. No database needed — these refuse before anything is read."""

import pytest

from modification.diff import generate_unified_diff
from modification.exceptions import ChangeIntegrityError, ProtectedFileError
from modification.models import ProposedChange
from modification.service import ModificationService
from rag.llm.stub_provider import StubLLMProvider
from tools.exceptions import ToolAuthorizationError
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider

ORIGINAL = "x = 1\n"


def _service():
    return ModificationService(None, DeterministicLocalEmbeddingProvider(), StubLLMProvider())  # type: ignore[arg-type]


def _change(path, original, new):
    return ProposedChange(path, original, new, generate_unified_diff(original, new, path))


@pytest.mark.parametrize(
    "target",
    [".env", ".ENV", "config/.env.production", "id_rsa", "ID_RSA", "deploy.pem", ".git/config", ".git/hooks/pre-commit",
     ".GIT/hooks/post-checkout", "node_modules/pkg/index.js", "src/__pycache__/m.py", ".venv/bin/activate"],
)
def test_protected_targets_are_never_written(tmp_path, target):
    path = tmp_path / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ORIGINAL)

    with pytest.raises(ProtectedFileError):
        _service().apply_change(str(tmp_path), _change(target, ORIGINAL, "pwned\n"), approved=True)
    assert path.read_text() == ORIGINAL


def test_a_protected_target_is_refused_even_when_it_does_not_exist_yet(tmp_path):
    with pytest.raises(ProtectedFileError):
        _service().apply_change(str(tmp_path), _change(".git/hooks/pre-push", "", "#!/bin/sh\n"), approved=True)
    assert not (tmp_path / ".git").exists()


def test_content_that_does_not_reproduce_the_approved_diff_is_refused(tmp_path):
    (tmp_path / "main.py").write_text(ORIGINAL)
    approved_diff = generate_unified_diff(ORIGINAL, "x = 2\n", "main.py")
    tampered = ProposedChange("main.py", ORIGINAL, "import os\nos.system('evil')\n", approved_diff)

    with pytest.raises(ChangeIntegrityError):
        _service().apply_change(str(tmp_path), tampered, approved=True)
    assert (tmp_path / "main.py").read_text() == ORIGINAL


def test_a_consistent_change_still_applies(tmp_path):
    (tmp_path / "main.py").write_text(ORIGINAL)
    result = _service().apply_change(str(tmp_path), _change("main.py", ORIGINAL, "x = 2\n"), approved=True)
    assert result.applied and (tmp_path / "main.py").read_text() == "x = 2\n"


def test_path_confinement_is_still_checked_first(tmp_path):
    with pytest.raises(ToolAuthorizationError):
        _service().apply_change(str(tmp_path), _change("../outside.py", "", "x\n"), approved=True)


def test_ordinary_files_with_lookalike_names_are_not_protected(tmp_path):
    for name in ("environment.py", "git_utils.py", "src/node_modules_helper.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ORIGINAL)
        assert _service().apply_change(str(tmp_path), _change(name, ORIGINAL, "x = 3\n"), approved=True).applied


# -- symlinks (audit fix): the policy applies to where the write LANDS -------
#
# A cloned repository is untrusted and may commit symlinks. Before this fix
# the protected-target check only looked at the path as written, so a
# symlinked directory or file let an approved change land in `.git/hooks`
# or `.env`. (Not reachable through the agent today — ingestion never
# indexes symlinks — but apply_change is the last boundary and must not
# depend on that.)


def _symlinked_dir_to_git_hooks(root):
    (root / ".git" / "hooks").mkdir(parents=True)
    (root / ".git" / "hooks" / "pre-commit").write_text(ORIGINAL)
    (root / "tools").symlink_to(".git/hooks", target_is_directory=True)
    return "tools/pre-commit", root / ".git" / "hooks" / "pre-commit"


def _symlinked_file_to_env(root):
    (root / ".env").write_text(ORIGINAL)
    (root / "notes.txt").symlink_to(".env")
    return "notes.txt", root / ".env"


def _symlinked_dir_to_node_modules(root):
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text(ORIGINAL)
    (root / "src").mkdir()
    (root / "src" / "vendor").symlink_to("../node_modules/pkg", target_is_directory=True)
    return "src/vendor/index.js", root / "node_modules" / "pkg" / "index.js"


SYMLINK_ESCAPES = [_symlinked_dir_to_git_hooks, _symlinked_file_to_env, _symlinked_dir_to_node_modules]


@pytest.mark.parametrize("setup", SYMLINK_ESCAPES, ids=lambda f: f.__name__)
def test_apply_change_refuses_a_symlink_into_a_protected_target(tmp_path, setup):
    link_path, real_target = setup(tmp_path)

    with pytest.raises(ProtectedFileError):
        _service().apply_change(str(tmp_path), _change(link_path, ORIGINAL, "pwned\n"), approved=True)
    assert real_target.read_text() == ORIGINAL


@pytest.mark.parametrize("setup", SYMLINK_ESCAPES, ids=lambda f: f.__name__)
def test_propose_change_refuses_a_symlink_into_a_protected_target(tmp_path, monkeypatch, setup):
    link_path, real_target = setup(tmp_path)
    monkeypatch.setattr("modification.service.find_affected_file", lambda *args: link_path)

    with pytest.raises(ProtectedFileError):
        _service().propose_change(str(tmp_path), 1, "change it")
    assert real_target.read_text() == ORIGINAL


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_apply_change_refuses_a_symlink_out_of_the_repository(tmp_path, link_kind):
    """Confinement at the write boundary itself (the tools' path checks
    were already covered in tests/security/test_path_traversal.py)."""
    root, outside = tmp_path / "repo", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "victim.py").write_text(ORIGINAL)
    if link_kind == "file":
        (root / "escape.py").symlink_to(outside / "victim.py")
        link_path = "escape.py"
    else:
        (root / "escape_dir").symlink_to(outside, target_is_directory=True)
        link_path = "escape_dir/victim.py"

    with pytest.raises(ToolAuthorizationError):
        _service().apply_change(str(root), _change(link_path, ORIGINAL, "pwned\n"), approved=True)
    assert (outside / "victim.py").read_text() == ORIGINAL
    assert sorted(p.name for p in outside.iterdir()) == ["victim.py"]


def test_a_symlink_to_an_ordinary_file_is_still_writable(tmp_path):
    (tmp_path / "real.py").write_text(ORIGINAL)
    (tmp_path / "alias.py").symlink_to("real.py")

    assert _service().apply_change(str(tmp_path), _change("alias.py", ORIGINAL, "x = 4\n"), approved=True).applied
    assert (tmp_path / "real.py").read_text() == "x = 4\n"
