from ingestion.service import IngestionService
from parsing.service import ParsingService


def test_parsing_service_parses_ingested_python_and_js_files(tmp_path):
    (tmp_path / "main.py").write_text("def hello():\n    return 'hi'\n")
    (tmp_path / "app.js").write_text("function hi() { return 'hi'; }\n")
    (tmp_path / "query.sql").write_text("SELECT * FROM users;\n")
    (tmp_path / "README.md").write_text("# Readme\n")

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)

    parsed_by_path = {p.relative_path: p for p in parsing_result.parsed_files}
    assert "main.py" in parsed_by_path
    assert parsed_by_path["main.py"].functions[0].name == "hello"
    assert "app.js" in parsed_by_path
    assert parsed_by_path["app.js"].functions[0].name == "hi"

    # README.md is category "documentation", never attempted.
    assert "README.md" not in parsed_by_path

    # query.sql is category "source" but has no entity extractor yet.
    skipped_paths = {path for path, _ in parsing_result.skipped_files}
    assert "query.sql" in skipped_paths


def test_parsing_service_handles_empty_repository(tmp_path):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)

    assert parsing_result.parsed_files == []
    assert parsing_result.skipped_files == []
    assert parsing_result.errors == []
