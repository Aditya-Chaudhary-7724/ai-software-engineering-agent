"""Repository-content security (Phase 14, sections 1 & 10): a target
repository is UNTRUSTED input, and may contain committed secrets. Two
independent chokepoints must both refuse to expose them:
1. Ingestion (the RAG/LLM path) — `.env`/`id_rsa`/etc. must never be
   chunked, embedded, or become retrievable context.
2. The direct file-access tools (`read_file`/`analyze_code`) — a
   caller must not be able to bypass (1) by asking for the file
   directly, and a single oversized file must not be read fully into
   memory before any limit applies.

These are real regression tests for a genuine gap found during this
phase's audit (see docs/security.md) — not hypothetical.
"""

import pytest

from ingestion.filters import DEFAULT_MAX_FILE_SIZE_BYTES, FilterConfig, is_sensitive_filename
from ingestion.service import IngestionService

from tools.exceptions import ToolAuthorizationError, ToolInputError
from tools.file_tools import analyze_code, read_file
from tools.schemas import AnalyzeCodeInput, ReadFileInput


@pytest.mark.parametrize(
    "filename",
    [".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "server.pem", "private.key", "credentials.json", ".netrc"],
)
def test_is_sensitive_filename_flags_known_credential_patterns(filename):
    assert is_sensitive_filename(filename) is True


@pytest.mark.parametrize("filename", ["main.py", "id_rsa.pub", "README.md", "keyword_search.py"])
def test_is_sensitive_filename_does_not_flag_ordinary_files(filename):
    assert is_sensitive_filename(filename) is False


def test_is_sensitive_filename_also_flags_env_example_like_siblings_by_design():
    """`.env.*` deliberately catches `.env.example`/`.env.local`/etc. too
    — distinguishing a real secret from a placeholder template by
    filename alone isn't reliable, so this errs toward exclusion. A
    false positive here (an ingested repo's harmless `.env.example`
    isn't indexed) is an acceptable, documented tradeoff against the
    alternative (a real `.env.production` slipping through).
    """
    assert is_sensitive_filename(".env.example") is True


def test_ingestion_excludes_sensitive_files_from_the_relevant_set(tmp_path):
    (tmp_path / ".env").write_text("GITHUB_TOKEN=ghp_realtokenvalue1234567890")
    (tmp_path / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    result = IngestionService().ingest(str(tmp_path))

    relevant_paths = {f.relative_path for f in result.files}
    assert relevant_paths == {"main.py"}
    ignored_reasons = {f.relative_path: f.reason for f in result.ignored_files}
    assert ignored_reasons[".env"] == "sensitive_file"
    assert ignored_reasons["id_rsa"] == "sensitive_file"


def test_a_sensitive_file_never_becomes_retrievable_rag_context(tmp_path):
    """The end-to-end property that actually matters: a secret in the
    repository must never reach chunking/embedding, since that's what
    makes it retrievable and therefore sendable to an LLM as context.
    """
    from vectorstore.chunking import build_chunks
    from parsing.service import ParsingService

    (tmp_path / ".env").write_text("DATABASE_PASSWORD=hunter2")
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    chunks = build_chunks(str(tmp_path), ingestion_result, parsing_result)

    assert all(chunk.relative_path != ".env" for chunk in chunks)
    assert all("hunter2" not in chunk.content for chunk in chunks)


def test_read_file_refuses_a_sensitive_file_even_when_asked_directly(tmp_path):
    (tmp_path / ".env").write_text("GITHUB_TOKEN=ghp_realtokenvalue1234567890")

    with pytest.raises(ToolAuthorizationError):
        read_file(ReadFileInput(root_path=str(tmp_path), relative_path=".env"))


def test_analyze_code_refuses_a_sensitive_file_even_when_asked_directly(tmp_path):
    (tmp_path / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----")

    with pytest.raises(ToolAuthorizationError):
        analyze_code(AnalyzeCodeInput(root_path=str(tmp_path), relative_path="id_rsa"))


def test_read_file_refuses_an_oversized_file_before_reading_it_fully(tmp_path):
    oversized = tmp_path / "huge.py"
    oversized.write_bytes(b"#" + b"x" * (DEFAULT_MAX_FILE_SIZE_BYTES + 1))

    with pytest.raises(ToolInputError, match="exceeds"):
        read_file(ReadFileInput(root_path=str(tmp_path), relative_path="huge.py"))


def test_read_file_still_works_normally_for_an_ordinary_file(tmp_path):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    result = read_file(ReadFileInput(root_path=str(tmp_path), relative_path="main.py"))

    assert "def greet" in result.content
    assert result.truncated is False


def test_filter_config_sensitive_patterns_are_configurable_not_hardcoded_globally():
    """Confirms the check goes through the same configurable FilterConfig
    as every other ingestion policy — an integrator could relax or
    extend it deliberately, not just accept a hidden global.
    """
    permissive = FilterConfig(sensitive_file_patterns=())
    assert is_sensitive_filename(".env", permissive) is False
