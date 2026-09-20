"""Code knowledge graph (Phase 5).

Builds a Neo4j graph of structural relationships (Repository -> Folder
-> File -> Class/Function, imports, inheritance) from Phase 1 + Phase 2
output. Only relationships that can actually be derived are created —
see builder.py's module docstring for exactly what is and isn't
attempted, and why (no hallucinated CALLS/USES/DEPENDS_ON edges).
"""

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.exceptions import GraphError, MissingCredentialsError
from graph.models import GraphBuildResult
from graph.schema import apply_constraints

__all__ = [
    "GraphBuilder",
    "Neo4jClient",
    "GraphError",
    "MissingCredentialsError",
    "GraphBuildResult",
    "apply_constraints",
]
