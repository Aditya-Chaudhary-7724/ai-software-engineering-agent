"""Pure unit tests — no Docker involved. Uses isolated tmp_path
repositories throughout, never the real project repository.
"""

from sandbox.command_detection import detect_test_command


def test_detects_pytest_by_test_prefixed_file(tmp_path):
    (tmp_path / "test_greet.py").write_text("def test_ok():\n    assert True\n")

    assert detect_test_command(str(tmp_path)) == ["python", "-m", "pytest", "-q"]


def test_detects_pytest_by_test_suffixed_file(tmp_path):
    (tmp_path / "greet_test.py").write_text("def test_ok():\n    assert True\n")

    assert detect_test_command(str(tmp_path)) == ["python", "-m", "pytest", "-q"]


def test_detects_pytest_by_config_file_even_without_test_files(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    assert detect_test_command(str(tmp_path)) == ["python", "-m", "pytest", "-q"]


def test_returns_none_for_a_plain_repository(tmp_path):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")

    assert detect_test_command(str(tmp_path)) is None


def test_returns_none_for_an_empty_repository(tmp_path):
    assert detect_test_command(str(tmp_path)) is None


def test_a_non_python_file_named_like_a_test_does_not_trigger_detection(tmp_path):
    (tmp_path / "test_notes.md").write_text("# not a test file\n")

    assert detect_test_command(str(tmp_path)) is None
