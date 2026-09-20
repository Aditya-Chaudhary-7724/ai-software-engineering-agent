"""graph_query, get_dependencies, get_callers, get_callees.

graph_query deliberately exposes a fixed enum of pre-built, parameterized
Cypher queries (from graph/queries.py) — never raw Cypher text from the
caller. Letting an agent submit arbitrary Cypher would be the graph
-database equivalent of exposing arbitrary shell execution, which this
project's own security principles explicitly rule out.

get_callers/get_callees are the one place Phase 8's tool list can't be
honestly implemented: Phase 5's graph has no CALLS relationship (Phase 2
doesn't extract call-sites inside function bodies — see
graph/builder.py's module docstring), so there's no real data to answer
"who calls this?" with. Rather than approximate it with name-matching
(which would silently produce false positives/negatives an agent can't
tell from a real answer), both tools return `available=False` with a
clear reason.
"""

from typing import Callable

from graph.client import Neo4jClient
from graph.queries import get_class_ancestors, get_file_dependencies, get_importers_of_file, get_repository_summary

from tools.exceptions import ToolInputError
from tools.schemas import (
    Dependency,
    GetCallersInput,
    GetCallersOutput,
    GetDependenciesInput,
    GetDependenciesOutput,
    GraphQueryInput,
    GraphQueryOutput,
)

VALID_QUERY_TYPES = frozenset({"repository_summary", "importers", "class_ancestors"})

CALLS_NOT_AVAILABLE_REASON = (
    "No CALLS relationship exists in the graph: Phase 2 (code parsing) extracts "
    "definitions (functions, classes, imports), not call-sites inside function "
    "bodies, so there is no real data to answer this from. Approximating it via "
    "name-matching was deliberately avoided to prevent false positives/negatives "
    "an agent could not distinguish from a real answer. This requires call-site "
    "extraction to be added to parsing first."
)


def make_graph_query(neo4j_client: Neo4jClient) -> Callable[[GraphQueryInput], GraphQueryOutput]:
    def graph_query(input_data: GraphQueryInput) -> GraphQueryOutput:
        if input_data.query_type not in VALID_QUERY_TYPES:
            raise ToolInputError(
                f"Unknown query_type '{input_data.query_type}'. Valid options: {sorted(VALID_QUERY_TYPES)}."
            )

        if input_data.query_type == "repository_summary":
            summary = get_repository_summary(neo4j_client, input_data.root_path)
            results = [summary]
        elif input_data.query_type == "importers":
            if not input_data.relative_path:
                raise ToolInputError("query_type 'importers' requires relative_path.")
            results = get_importers_of_file(neo4j_client, input_data.root_path, input_data.relative_path)
        else:  # class_ancestors
            if not input_data.relative_path or not input_data.class_name:
                raise ToolInputError("query_type 'class_ancestors' requires relative_path and class_name.")
            results = get_class_ancestors(
                neo4j_client, input_data.root_path, input_data.relative_path, input_data.class_name
            )

        return GraphQueryOutput(query_type=input_data.query_type, results=results)

    return graph_query


def make_get_dependencies(neo4j_client: Neo4jClient) -> Callable[[GetDependenciesInput], GetDependenciesOutput]:
    def get_dependencies(input_data: GetDependenciesInput) -> GetDependenciesOutput:
        rows = get_file_dependencies(neo4j_client, input_data.root_path, input_data.relative_path)
        dependencies = [Dependency(module=r["module"], resolved_file=r["resolved_file"]) for r in rows]
        return GetDependenciesOutput(dependencies=dependencies)

    return get_dependencies


def get_callers(input_data: GetCallersInput) -> GetCallersOutput:
    return GetCallersOutput(available=False, reason=CALLS_NOT_AVAILABLE_REASON)


def get_callees(input_data: GetCallersInput) -> GetCallersOutput:
    return GetCallersOutput(available=False, reason=CALLS_NOT_AVAILABLE_REASON)
