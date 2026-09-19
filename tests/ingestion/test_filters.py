from pathlib import Path

import pytest

from ingestion.filters import FilterConfig, classify_file, is_ignored_directory


@pytest.mark.parametrize(
    "dir_name", ["node_modules", "__pycache__", ".git", ".venv", "dist", "build"]
)
def test_is_ignored_directory_true_for_known_names(dir_name):
    assert is_ignored_directory(dir_name, FilterConfig())


def test_is_ignored_directory_false_for_source_directory():
    assert not is_ignored_directory("src", FilterConfig())


def test_classify_file_relevant_text_file(tmp_path):
    file_path = tmp_path / "main.py"
    file_path.write_text("print('hello')\n")

    is_relevant, reason = classify_file(file_path, "main.py", file_path.stat().st_size, FilterConfig())

    assert is_relevant is True
    assert reason is None


def test_classify_file_ignores_known_binary_extension(tmp_path):
    file_path = tmp_path / "logo.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    is_relevant, reason = classify_file(file_path, "logo.png", file_path.stat().st_size, FilterConfig())

    assert is_relevant is False
    assert reason == "binary_extension"


def test_classify_file_ignores_generated_artifact_pattern(tmp_path):
    file_path = tmp_path / "bundle.min.js"
    file_path.write_text("(function(){})();")

    is_relevant, reason = classify_file(
        file_path, "bundle.min.js", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is False
    assert reason == "generated_artifact"


def test_classify_file_ignores_oversized_file(tmp_path):
    file_path = tmp_path / "data.txt"
    file_path.write_text("x" * 100)
    config = FilterConfig(max_file_size_bytes=10)

    is_relevant, reason = classify_file(file_path, "data.txt", file_path.stat().st_size, config)

    assert is_relevant is False
    assert reason == "file_too_large"


def test_classify_file_size_limit_is_configurable(tmp_path):
    file_path = tmp_path / "data.txt"
    file_path.write_text("x" * 100)

    lenient_config = FilterConfig(max_file_size_bytes=1000)
    is_relevant, reason = classify_file(
        file_path, "data.txt", file_path.stat().st_size, lenient_config
    )

    assert is_relevant is True
    assert reason is None


def test_classify_file_sniffs_unknown_extension_with_binary_content(tmp_path):
    file_path = tmp_path / "payload.dat"
    file_path.write_bytes(b"\x00\x01\x02binary")

    is_relevant, reason = classify_file(
        file_path, "payload.dat", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is False
    assert reason == "binary_content"


def test_classify_file_does_not_blindly_ignore_unknown_text_extension(tmp_path):
    file_path = tmp_path / "notes.xyz"
    file_path.write_text("just plain text content")

    is_relevant, reason = classify_file(
        file_path, "notes.xyz", file_path.stat().st_size, FilterConfig()
    )

    assert is_relevant is True
    assert reason is None
