"""Turns Phase 1 (ingestion) + Phase 2 (parsing) output into CodeChunks.

Chunking strategy:
- If a file was successfully parsed and produced at least one class or
  function, one chunk is created per class and per function/method,
  with `content` sliced directly from the file's source lines using
  the parser's line ranges. A class chunk's content overlaps with its
  methods' chunks (the class body naturally contains them) — this is
  intentional: the class chunk gives broad context for "what does this
  class do", while method chunks give focused context for "what does
  this one method do".
- If a file has no extracted symbols (e.g. SQL, or a source file that
  produced zero classes/functions), the whole file becomes one "file"
  chunk, so nothing indexable is silently dropped.

Limitation: documentation/config files (README.md, package.json, etc.)
are not chunked in Phase 3. Indexing them is a reasonable future
extension but is out of scope here, which focuses on code.
"""

from pathlib import Path

from ingestion.models import IngestionResult
from parsing.models import ParsedFile, ParsingResult
from vectorstore.models import CodeChunk


def _slice_lines(lines: list[str], start_line: int, end_line: int) -> str:
    """start_line/end_line are 1-indexed and inclusive."""
    return "\n".join(lines[start_line - 1 : end_line])


def _chunks_for_parsed_file(parsed: ParsedFile, lines: list[str]) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []

    for cls in parsed.classes:
        chunks.append(
            CodeChunk(
                relative_path=parsed.relative_path,
                language=parsed.language,
                chunk_type="class",
                symbol_name=cls.name,
                qualified_name=cls.name,
                start_line=cls.start_line,
                end_line=cls.end_line,
                content=_slice_lines(lines, cls.start_line, cls.end_line),
            )
        )

    for func in parsed.functions:
        chunks.append(
            CodeChunk(
                relative_path=parsed.relative_path,
                language=parsed.language,
                chunk_type="function",
                symbol_name=func.name,
                qualified_name=func.qualified_name,
                start_line=func.start_line,
                end_line=func.end_line,
                content=_slice_lines(lines, func.start_line, func.end_line),
            )
        )

    return chunks


def build_chunks(
    repository_root: str, ingestion_result: IngestionResult, parsing_result: ParsingResult
) -> list[CodeChunk]:
    root = Path(repository_root)
    parsed_by_path = {p.relative_path: p for p in parsing_result.parsed_files}

    chunks: list[CodeChunk] = []

    for file_meta in ingestion_result.files:
        if file_meta.category != "source":
            continue

        parsed = parsed_by_path.get(file_meta.relative_path)

        try:
            text = (root / file_meta.relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if parsed is not None and (parsed.classes or parsed.functions):
            lines = text.splitlines()
            chunks.extend(_chunks_for_parsed_file(parsed, lines))
        else:
            line_count = text.count("\n") + 1 if text else 0
            chunks.append(
                CodeChunk(
                    relative_path=file_meta.relative_path,
                    language=file_meta.language,
                    chunk_type="file",
                    symbol_name=None,
                    qualified_name=None,
                    start_line=1,
                    end_line=line_count,
                    content=text,
                )
            )

    return chunks
