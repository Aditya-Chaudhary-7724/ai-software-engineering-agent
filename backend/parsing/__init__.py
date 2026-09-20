"""Code parsing subsystem (Phase 2).

Takes a Phase 1 IngestionResult and extracts structured symbol
metadata (functions, classes, imports, line ranges) from every source
file it knows how to parse: Python via the standard library `ast`
module, and JavaScript/JSX/TypeScript/TSX/Java/Go/C/C++ via Tree-sitter.
"""

from parsing.exceptions import ParsingError
from parsing.models import ClassEntity, FunctionEntity, ImportEntity, ParsedFile, ParsingResult
from parsing.service import ParsingService

__all__ = [
    "ParsingError",
    "ClassEntity",
    "FunctionEntity",
    "ImportEntity",
    "ParsedFile",
    "ParsingResult",
    "ParsingService",
]
