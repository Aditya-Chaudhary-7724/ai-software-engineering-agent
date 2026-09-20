"""Builds the default ToolRegistry with every Phase 8 tool registered."""

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from tools.file_tools import analyze_code, list_files, read_file
from tools.graph_tools import get_callees, get_callers, make_get_dependencies, make_graph_query
from tools.registry import Tool, ToolRegistry
from tools.schemas import (
    AnalyzeCodeInput,
    AnalyzeCodeOutput,
    GetCallersInput,
    GetCallersOutput,
    GetDependenciesInput,
    GetDependenciesOutput,
    GraphQueryInput,
    GraphQueryOutput,
    ListFilesInput,
    ListFilesOutput,
    ReadFileInput,
    ReadFileOutput,
    SearchCodeInput,
    SearchCodeOutput,
    SearchSymbolInput,
    SearchSymbolOutput,
)
from tools.search_tools import make_search_code, make_search_symbol


def build_default_registry(
    vector_store: VectorStore, neo4j_client: Neo4jClient, embedding_provider: EmbeddingProvider
) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        Tool(
            name="list_files",
            description="List files in the repository, optionally scoped to a subdirectory.",
            input_model=ListFilesInput,
            output_model=ListFilesOutput,
            handler=list_files,
        )
    )
    registry.register(
        Tool(
            name="read_file",
            description="Read a text file's content from the repository.",
            input_model=ReadFileInput,
            output_model=ReadFileOutput,
            handler=read_file,
        )
    )
    registry.register(
        Tool(
            name="analyze_code",
            description="Parse a single file and return its classes, functions, and imports.",
            input_model=AnalyzeCodeInput,
            output_model=AnalyzeCodeOutput,
            handler=analyze_code,
        )
    )
    registry.register(
        Tool(
            name="search_code",
            description="Semantic + keyword search over indexed code chunks in a repository.",
            input_model=SearchCodeInput,
            output_model=SearchCodeOutput,
            handler=make_search_code(vector_store, embedding_provider),
        )
    )
    registry.register(
        Tool(
            name="search_symbol",
            description="Find every class/function/method with an exact name, via the knowledge graph.",
            input_model=SearchSymbolInput,
            output_model=SearchSymbolOutput,
            handler=make_search_symbol(neo4j_client),
        )
    )
    registry.register(
        Tool(
            name="graph_query",
            description=(
                "Run one of a fixed set of pre-built graph queries "
                "(repository_summary / importers / class_ancestors) — never raw Cypher."
            ),
            input_model=GraphQueryInput,
            output_model=GraphQueryOutput,
            handler=make_graph_query(neo4j_client),
        )
    )
    registry.register(
        Tool(
            name="get_dependencies",
            description="List what a file imports, and which imports resolve to a real file in this repository.",
            input_model=GetDependenciesInput,
            output_model=GetDependenciesOutput,
            handler=make_get_dependencies(neo4j_client),
        )
    )
    registry.register(
        Tool(
            name="get_callers",
            description="Not available: requires call-site data Phase 2 does not extract (see graph_tools.py).",
            input_model=GetCallersInput,
            output_model=GetCallersOutput,
            handler=get_callers,
        )
    )
    registry.register(
        Tool(
            name="get_callees",
            description="Not available: requires call-site data Phase 2 does not extract (see graph_tools.py).",
            input_model=GetCallersInput,
            output_model=GetCallersOutput,
            handler=get_callees,
        )
    )

    return registry
