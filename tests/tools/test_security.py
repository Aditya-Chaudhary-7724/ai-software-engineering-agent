import pytest

from tools.exceptions import ToolAuthorizationError
from tools.security import resolve_safe_path


def test_resolve_safe_path_within_root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n")

    resolved = resolve_safe_path(str(tmp_path), "src/main.py")

    assert resolved == (tmp_path / "src" / "main.py").resolve()


def test_resolve_safe_path_rejects_parent_traversal(tmp_path):
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(tmp_path), "../../etc/passwd")


def test_resolve_safe_path_rejects_absolute_escape(tmp_path):
    with pytest.raises(ToolAuthorizationError):
        resolve_safe_path(str(tmp_path), "/etc/passwd")


def test_resolve_safe_path_allows_root_itself(tmp_path):
    resolved = resolve_safe_path(str(tmp_path), ".")
    assert resolved == tmp_path.resolve()
