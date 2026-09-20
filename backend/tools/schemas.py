"""Declarative input/output schemas for every tool.

Pydantic, not dataclasses, is used here specifically (unlike the rest
of the backend): tool inputs come from an LLM or an external caller
and need runtime validation with clear error messages, and JSON Schema
generation (`Model.model_json_schema()`) is what a future LLM
tool-calling registration would need to hand the model — dataclasses
provide neither. This is the first place in the project with that
concrete, external-input-validation need; internal data models
elsewhere stay plain dataclasses.
"""

from typing import Optional

from pydantic import BaseModel, Field


class ListFilesInput(BaseModel):
    root_path: str
    directory: str = Field(default="", description="Relative subdirectory to list; '' lists the whole repository.")


class FileEntry(BaseModel):
    relative_path: str
    language: Optional[str]
    category: str
    size_bytes: int


class ListFilesOutput(BaseModel):
    files: list[FileEntry]


class ReadFileInput(BaseModel):
    root_path: str
    relative_path: str


class ReadFileOutput(BaseModel):
    relative_path: str
    content: str
    truncated: bool


class SearchCodeInput(BaseModel):
    repository_id: int
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    language: Optional[str] = None


class SearchCodeHit(BaseModel):
    relative_path: str
    chunk_type: str
    symbol_name: Optional[str]
    start_line: int
    end_line: int
    content: str
    found_via: list[str]


class SearchCodeOutput(BaseModel):
    hits: list[SearchCodeHit]


class SearchSymbolInput(BaseModel):
    root_path: str
    name: str


class SymbolMatch(BaseModel):
    kind: str
    relative_path: str
    name: str


class SearchSymbolOutput(BaseModel):
    matches: list[SymbolMatch]


class AnalyzeCodeInput(BaseModel):
    root_path: str
    relative_path: str


class AnalyzeCodeOutput(BaseModel):
    relative_path: str
    language: str
    imports: list[str]
    classes: list[str]
    functions: list[str]
    parse_errors: list[str]


class GetDependenciesInput(BaseModel):
    root_path: str
    relative_path: str


class Dependency(BaseModel):
    module: str
    resolved_file: Optional[str]


class GetDependenciesOutput(BaseModel):
    dependencies: list[Dependency]


class GraphQueryInput(BaseModel):
    root_path: str
    query_type: str = Field(
        description="One of: 'repository_summary', 'importers', 'class_ancestors'. "
        "Not raw Cypher — see graph_tools.py for why."
    )
    relative_path: Optional[str] = None
    class_name: Optional[str] = None


class GraphQueryOutput(BaseModel):
    query_type: str
    results: list[dict]


class GetCallersInput(BaseModel):
    root_path: str
    relative_path: str
    symbol_name: str


class GetCallersOutput(BaseModel):
    available: bool
    reason: str
    callers: list[dict] = Field(default_factory=list)
