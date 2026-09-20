from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.chunking import build_chunks


def test_build_chunks_one_per_class_and_function(tmp_path):
    (tmp_path / "animals.py").write_text(
        "class Animal:\n"
        "    def speak(self):\n"
        "        return '...'\n\n"
        "def helper():\n"
        "    return 1\n"
    )

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    chunks = build_chunks(str(tmp_path), ingestion_result, parsing_result)

    by_type = {(c.chunk_type, c.symbol_name) for c in chunks}
    assert ("class", "Animal") in by_type
    assert ("function", "speak") in by_type
    assert ("function", "helper") in by_type

    class_chunk = next(c for c in chunks if c.symbol_name == "Animal")
    assert "def speak" in class_chunk.content


def test_build_chunks_falls_back_to_whole_file_when_no_symbols(tmp_path):
    (tmp_path / "query.sql").write_text("SELECT * FROM users;\n")

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    chunks = build_chunks(str(tmp_path), ingestion_result, parsing_result)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "file"
    assert chunks[0].relative_path == "query.sql"
    assert "SELECT" in chunks[0].content


def test_build_chunks_skips_non_source_files(tmp_path):
    (tmp_path / "README.md").write_text("# Docs\n")

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    chunks = build_chunks(str(tmp_path), ingestion_result, parsing_result)

    assert chunks == []


def test_build_chunks_empty_repository(tmp_path):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    chunks = build_chunks(str(tmp_path), ingestion_result, parsing_result)

    assert chunks == []
