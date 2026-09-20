"""Structured symbol metadata produced by code parsing.

Line numbers are 1-indexed and inclusive on both ends, matching how
editors and diff tools display them.

`qualified_name` is file-relative (e.g. "ClassName.method_name"), not a
full module path — resolving full module paths across a package/import
graph is a Phase 5 (knowledge graph) concern, not parsing.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ImportEntity:
    """A single import statement."""

    module: str
    alias: Optional[str]
    line: int


@dataclass(frozen=True)
class FunctionEntity:
    """A function or method definition."""

    name: str
    qualified_name: str
    start_line: int
    end_line: int
    parameters: tuple[str, ...]
    is_method: bool
    parent_class: Optional[str]


@dataclass(frozen=True)
class ClassEntity:
    """A class (or struct/interface, where the language has an equivalent) definition."""

    name: str
    start_line: int
    end_line: int
    base_classes: tuple[str, ...]


@dataclass
class ParsedFile:
    """Symbols extracted from a single source file."""

    relative_path: str
    language: str
    imports: list[ImportEntity] = field(default_factory=list)
    classes: list[ClassEntity] = field(default_factory=list)
    functions: list[FunctionEntity] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


@dataclass
class ParsingResult:
    """The full structured output of a parsing run over an ingested repository."""

    parsed_files: list[ParsedFile] = field(default_factory=list)
    skipped_files: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
