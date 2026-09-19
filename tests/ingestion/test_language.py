import pytest

from ingestion.language import categorize, detect_language


@pytest.mark.parametrize(
    "extension,expected_language",
    [
        (".py", "Python"),
        (".js", "JavaScript"),
        (".jsx", "JSX"),
        (".ts", "TypeScript"),
        (".tsx", "TSX"),
        (".java", "Java"),
        (".c", "C"),
        (".h", "C"),
        (".cpp", "C++"),
        (".go", "Go"),
        (".html", "HTML"),
        (".css", "CSS"),
        (".sql", "SQL"),
        (".json", "JSON"),
        (".yaml", "YAML"),
        (".yml", "YAML"),
    ],
)
def test_detect_language_known_extensions(extension, expected_language):
    assert detect_language(extension) == expected_language


def test_detect_language_is_case_insensitive():
    assert detect_language(".PY") == "Python"


def test_detect_language_unknown_extension_returns_none():
    assert detect_language(".rs") is None
    assert detect_language("") is None


def test_categorize_source_language():
    assert categorize(".py", "Python") == "source"


def test_categorize_markup_language():
    assert categorize(".html", "HTML") == "markup"


def test_categorize_config_language():
    assert categorize(".json", "JSON") == "config"


def test_categorize_documentation_extension_override():
    assert categorize(".md", None) == "documentation"


def test_categorize_unknown_falls_back_to_other():
    assert categorize(".rs", None) == "other"
