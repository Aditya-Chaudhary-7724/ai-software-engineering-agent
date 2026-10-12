"""Case-variant sensitive filenames must be refused by the direct
file-access tools (`read_file`/`analyze_code`).

Real vulnerability, found during frontend phase F1 and reproduced
before the fix: `is_sensitive_filename` matches with `fnmatch`, which
is case-SENSITIVE on POSIX, while macOS (APFS) and Windows filesystems
are case-INSENSITIVE by default. Requesting `.ENV` therefore passed the
`.env` pattern check but opened the real `.env` — returning its
secrets. A second, related gap: the existence check ran BEFORE the
sensitive-name check, so the error differed for a missing vs. an
existing `.env` (an existence oracle).

These tests hold on both kinds of filesystem: the name check runs
before any filesystem access, so the outcome never depends on whether
— or under which case — the file exists.
"""

import pytest

from ingestion.filters import DEFAULT_MAX_FILE_SIZE_BYTES

from tools.exceptions import ToolAuthorizationError, ToolInputError
from tools.file_tools import analyze_code, read_file
from tools.schemas import AnalyzeCodeInput, ReadFileInput

SECRET = "GITHUB_TOKEN=ghp_realtokenvalue1234567890"

CASE_VARIANTS = [".ENV", ".Env", ".ENV.LOCAL", ".env.Production", "ID_RSA", "Id_Ed25519", "Server.PEM", "PRIVATE.KEY", "Credentials.JSON", ".NETRC"]


def _filesystem_is_case_insensitive(directory) -> bool:
    probe = directory / "case_probe"
    probe.write_text("x")
    try:
        return (directory / "CASE_PROBE").exists()
    finally:
        probe.unlink()


def _read(root, relative_path):
    return read_file(ReadFileInput(root_path=str(root), relative_path=relative_path))


def _analyze(root, relative_path):
    return analyze_code(AnalyzeCodeInput(root_path=str(root), relative_path=relative_path))


@pytest.mark.parametrize("tool", [_read, _analyze])
def test_upper_case_request_cannot_open_the_real_lower_case_env_file(tmp_path, tool):
    """The original exploit: `.env` exists on disk; `.ENV` is requested."""
    (tmp_path / ".env").write_text(SECRET)
    if _filesystem_is_case_insensitive(tmp_path):
        # Precondition: on this filesystem the variant really does reach the secret.
        assert (tmp_path / ".ENV").read_text() == SECRET

    with pytest.raises(ToolAuthorizationError) as exc_info:
        tool(tmp_path, ".ENV")
    assert SECRET not in str(exc_info.value)


@pytest.mark.parametrize("tool", [_read, _analyze])
@pytest.mark.parametrize("filename", CASE_VARIANTS)
def test_case_variant_sensitive_files_are_refused(tmp_path, tool, filename):
    """A file whose on-disk name IS the case variant (as on a
    case-sensitive filesystem, or a repo cloned with that name)."""
    (tmp_path / filename).write_text(SECRET)

    with pytest.raises(ToolAuthorizationError):
        tool(tmp_path, filename)


@pytest.mark.parametrize("tool", [_read, _analyze])
@pytest.mark.parametrize("relative_path", [".env", ".ENV", "nested/.env.local", "nested/ID_RSA", "missing.pem"])
def test_refusal_does_not_depend_on_the_file_existing(tmp_path, tool, relative_path):
    """No existence oracle: a sensitive name is refused identically
    whether or not the file is there."""
    with pytest.raises(ToolAuthorizationError):
        tool(tmp_path, relative_path)


@pytest.mark.parametrize("tool", [_read, _analyze])
def test_symlink_renaming_cannot_bypass_the_check(tmp_path, tool):
    (tmp_path / ".env").write_text(SECRET)
    (tmp_path / "notes.py").symlink_to(tmp_path / ".env")  # innocent name -> sensitive target
    (tmp_path / "harmless.py").write_text("x = 1\n")
    (tmp_path / "Server.PEM").symlink_to(tmp_path / "harmless.py")  # sensitive name -> innocent target

    for name in ("notes.py", "Server.PEM"):
        with pytest.raises(ToolAuthorizationError):
            tool(tmp_path, name)


# --- The fix must not weaken existing protections or over-block ---


@pytest.mark.parametrize("tool", [_read, _analyze])
@pytest.mark.parametrize("relative_path", ["../outside.py", "../../etc/passwd", "/etc/passwd", "../.ENV"])
def test_path_traversal_is_still_refused(tmp_path, tool, relative_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.py").write_text("x = 1\n")

    with pytest.raises(ToolAuthorizationError):
        tool(root, relative_path)


def test_file_size_limit_is_still_enforced(tmp_path):
    (tmp_path / "HUGE.PY").write_bytes(b"#" + b"x" * (DEFAULT_MAX_FILE_SIZE_BYTES + 1))

    with pytest.raises(ToolInputError, match="exceeds"):
        _read(tmp_path, "HUGE.PY")


def test_missing_ordinary_file_still_reports_not_found(tmp_path):
    with pytest.raises(ToolInputError, match="File not found"):
        _read(tmp_path, "src/missing.py")


@pytest.mark.parametrize("filename", ["Main.PY", "README.MD", "ID_RSA.PUB", "Keyword_Search.py", "environment.py"])
def test_ordinary_mixed_case_files_are_still_readable(tmp_path, filename):
    (tmp_path / filename).write_text("def greet():\n    return 'hi'\n")

    assert "def greet" in _read(tmp_path, filename).content


def test_analyze_code_still_works_for_an_ordinary_file(tmp_path):
    (tmp_path / "Service.py").write_text("class Greeter:\n    def greet(self):\n        return 'hi'\n")

    result = _analyze(tmp_path, "Service.py")

    assert result.classes == ["Greeter"]
    assert "Greeter.greet" in result.functions
