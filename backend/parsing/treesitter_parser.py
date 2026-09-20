"""Tree-sitter-backed parsing entry point.

Limitations, documented rather than silently worked around:
- C/C++ function name extraction relies on unwrapping pointer/reference
  declarators and, in C++, `qualified_identifier` (Class::method). This
  does not attempt to handle function pointers, templates, operator
  overloads, or macro-expanded declarators.
- Base class extraction for JS/TS `implements` clauses and Go embedded
  structs is not attempted (left as an empty tuple).
- Parameters are extracted as raw source text per named child of the
  parameter list, not decomposed into (name, type) pairs.
"""

from tree_sitter import Parser

from parsing.languages import EXTRACTORS, load_grammar
from parsing.models import ParsedFile


def parse_with_treesitter(source_bytes: bytes, relative_path: str, language: str) -> ParsedFile:
    parsed = ParsedFile(relative_path=relative_path, language=language)

    extractor = EXTRACTORS.get(language)
    if extractor is None:
        parsed.parse_errors.append(f"No Tree-sitter extractor configured for language: {language}")
        return parsed

    try:
        grammar = load_grammar(language)
        parser = Parser(grammar)
        tree = parser.parse(source_bytes)
        imports, classes, functions = extractor(tree.root_node, source_bytes)
    except Exception as exc:  # noqa: BLE001 - a malformed/unsupported file must not abort the run
        parsed.parse_errors.append(f"Tree-sitter parse error: {exc}")
        return parsed

    if tree.root_node.has_error:
        parsed.parse_errors.append("Source contains syntax errors; extraction may be incomplete.")

    parsed.imports = imports
    parsed.classes = classes
    parsed.functions = functions
    return parsed
