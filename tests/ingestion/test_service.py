import pytest

from ingestion.exceptions import InvalidRepositoryPathError
from ingestion.filters import FilterConfig
from ingestion.service import IngestionService


def build_sample_repository(root):
    """A small repository exercising every Phase 1 code path."""
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hello')\n")
    (root / "src" / "app.js").write_text("console.log('hi');\n")
    (root / "index.html").write_text("<html></html>\n")
    (root / "style.css").write_text("body { color: red; }\n")
    (root / "data.json").write_text('{"key": "value"}\n')
    (root / "README.md").write_text("# Sample\n")

    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = {};\n")

    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")

    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nrest-of-file")
    (root / "cached.pyc").write_bytes(b"\x00compiled")
    (root / "oversized.txt").write_text("x" * 50)


def test_ingest_full_repository_counts_and_reasons(tmp_path):
    build_sample_repository(tmp_path)
    service = IngestionService(FilterConfig(max_file_size_bytes=30))

    result = service.ingest(str(tmp_path))

    relevant_paths = {f.relative_path for f in result.files}
    assert relevant_paths == {
        "src/main.py",
        "src/app.js",
        "index.html",
        "style.css",
        "data.json",
        "README.md",
    }

    ignored_by_path = {f.relative_path: f.reason for f in result.ignored_files}
    assert ignored_by_path["logo.png"] == "binary_extension"
    assert ignored_by_path["cached.pyc"] == "generated_artifact"
    assert ignored_by_path["oversized.txt"] == "file_too_large"
    # node_modules/ and .git/ are pruned during traversal, so their
    # contents never appear as discovered or ignored files at all.
    assert "node_modules/pkg/index.js" not in ignored_by_path
    assert ".git/config" not in ignored_by_path

    assert result.repository.name == tmp_path.name
    assert result.repository.relevant_files == 6
    assert result.repository.ignored_files == 3
    assert result.repository.total_files_discovered == 9


def test_ingest_language_statistics(tmp_path):
    build_sample_repository(tmp_path)
    service = IngestionService(FilterConfig(max_file_size_bytes=30))

    result = service.ingest(str(tmp_path))

    assert result.repository.language_stats["Python"].file_count == 1
    assert result.repository.language_stats["JavaScript"].file_count == 1
    assert result.repository.language_stats["HTML"].file_count == 1
    assert result.repository.language_stats["CSS"].file_count == 1
    assert result.repository.language_stats["JSON"].file_count == 1
    # README.md has no language mapping in Phase 1.
    assert result.repository.language_stats["Unknown"].file_count == 1


def test_ingest_empty_repository(tmp_path):
    service = IngestionService()

    result = service.ingest(str(tmp_path))

    assert result.files == []
    assert result.ignored_files == []
    assert result.errors == []
    assert result.repository.total_files_discovered == 0
    assert result.repository.relevant_files == 0
    assert result.repository.ignored_files == 0


def test_ingest_nonexistent_path_raises(tmp_path):
    service = IngestionService()

    with pytest.raises(InvalidRepositoryPathError):
        service.ingest(str(tmp_path / "does-not-exist"))


def test_ingest_path_that_is_a_file_raises(tmp_path):
    file_path = tmp_path / "not_a_directory.txt"
    file_path.write_text("content")
    service = IngestionService()

    with pytest.raises(InvalidRepositoryPathError):
        service.ingest(str(file_path))


def test_ingest_size_limit_is_configurable(tmp_path):
    (tmp_path / "medium.txt").write_text("x" * 500)

    strict_service = IngestionService(FilterConfig(max_file_size_bytes=100))
    strict_result = strict_service.ingest(str(tmp_path))
    assert strict_result.ignored_files[0].reason == "file_too_large"

    lenient_service = IngestionService(FilterConfig(max_file_size_bytes=1000))
    lenient_result = lenient_service.ingest(str(tmp_path))
    assert lenient_result.ignored_files == []
    assert len(lenient_result.files) == 1
