import os
import sys

import pytest

from ingestion.filters import FilterConfig
from ingestion.scanner import discover_files


def relative_paths(discovered):
    return {entry.relative_path for entry in discovered}


def test_discover_files_in_nested_directories(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "deep.py").write_text("x = 1\n")
    (tmp_path / "top.py").write_text("y = 2\n")

    errors: list[str] = []
    discovered = discover_files(tmp_path, FilterConfig(), errors)

    assert relative_paths(discovered) == {"a/b/c/deep.py", "top.py"}
    assert errors == []


def test_discover_files_prunes_ignored_directories(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "file.js").write_text("module.exports = {};")
    (tmp_path / "main.py").write_text("print(1)\n")

    errors: list[str] = []
    discovered = discover_files(tmp_path, FilterConfig(), errors)

    assert relative_paths(discovered) == {"main.py"}


def test_discover_files_on_empty_repository(tmp_path):
    errors: list[str] = []
    discovered = discover_files(tmp_path, FilterConfig(), errors)

    assert discovered == []
    assert errors == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges on Windows")
def test_discover_files_skips_symlinked_file(tmp_path):
    real_file = tmp_path / "real.py"
    real_file.write_text("z = 3\n")
    symlink_file = tmp_path / "link.py"
    symlink_file.symlink_to(real_file)

    errors: list[str] = []
    discovered = discover_files(tmp_path, FilterConfig(), errors)

    assert relative_paths(discovered) == {"real.py"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges on Windows")
def test_discover_files_skips_symlinked_directory(tmp_path):
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "file.py").write_text("a = 1\n")
    (tmp_path / "link_dir").symlink_to(real_dir, target_is_directory=True)

    errors: list[str] = []
    discovered = discover_files(tmp_path, FilterConfig(), errors)

    assert relative_paths(discovered) == {"real_dir/file.py"}


@pytest.mark.skipif(sys.platform == "win32", reason="chmod-based permission test is POSIX-specific")
def test_discover_files_reports_inaccessible_directory(tmp_path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    (blocked_dir / "secret.py").write_text("s = 1\n")
    (tmp_path / "visible.py").write_text("v = 1\n")

    original_mode = blocked_dir.stat().st_mode
    os.chmod(blocked_dir, 0o000)
    try:
        errors: list[str] = []
        discovered = discover_files(tmp_path, FilterConfig(), errors)

        assert relative_paths(discovered) == {"visible.py"}
        assert len(errors) == 1
    finally:
        os.chmod(blocked_dir, original_mode)
