import pytest

from tools.exceptions import ToolAuthorizationError, ToolInputError
from tools.file_tools import analyze_code, list_files, read_file
from tools.schemas import AnalyzeCodeInput, ListFilesInput, ReadFileInput


def test_list_files_returns_all_files(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("# Docs\n")

    result = list_files(ListFilesInput(root_path=str(tmp_path)))

    paths = {f.relative_path for f in result.files}
    assert paths == {"main.py", "README.md"}


def test_list_files_scoped_to_directory(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n")
    (tmp_path / "docs.md").write_text("# Docs\n")

    result = list_files(ListFilesInput(root_path=str(tmp_path), directory="src"))

    assert [f.relative_path for f in result.files] == ["src/main.py"]


def test_read_file_returns_content(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n")

    result = read_file(ReadFileInput(root_path=str(tmp_path), relative_path="main.py"))

    assert result.content == "print('hi')\n"
    assert result.truncated is False


def test_read_file_truncates_oversized_content(tmp_path, monkeypatch):
    import tools.file_tools as file_tools

    monkeypatch.setattr(file_tools, "READ_FILE_MAX_CHARS", 10)
    (tmp_path / "big.py").write_text("x" * 100)

    result = read_file(ReadFileInput(root_path=str(tmp_path), relative_path="big.py"))

    assert result.truncated is True
    assert len(result.content) == 10


def test_read_file_missing_raises_tool_input_error(tmp_path):
    with pytest.raises(ToolInputError):
        read_file(ReadFileInput(root_path=str(tmp_path), relative_path="missing.py"))


def test_read_file_path_traversal_raises_authorization_error(tmp_path):
    with pytest.raises(ToolAuthorizationError):
        read_file(ReadFileInput(root_path=str(tmp_path), relative_path="../outside.py"))


def test_read_file_binary_file_raises_input_error(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02\xff")

    with pytest.raises(ToolInputError):
        read_file(ReadFileInput(root_path=str(tmp_path), relative_path="image.bin"))


def test_analyze_code_python_file(tmp_path):
    (tmp_path / "animals.py").write_text(
        "import os\n\nclass Animal:\n    def speak(self):\n        pass\n"
    )

    result = analyze_code(AnalyzeCodeInput(root_path=str(tmp_path), relative_path="animals.py"))

    assert result.language == "Python"
    assert result.classes == ["Animal"]
    assert result.functions == ["Animal.speak"]
    assert result.imports == ["os"]


def test_analyze_code_unsupported_language_raises(tmp_path):
    (tmp_path / "query.sql").write_text("SELECT * FROM users;\n")

    with pytest.raises(ToolInputError):
        analyze_code(AnalyzeCodeInput(root_path=str(tmp_path), relative_path="query.sql"))


def test_analyze_code_missing_file_raises(tmp_path):
    with pytest.raises(ToolInputError):
        analyze_code(AnalyzeCodeInput(root_path=str(tmp_path), relative_path="missing.py"))
