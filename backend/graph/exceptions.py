"""Exceptions raised by the knowledge graph subsystem."""


class GraphError(Exception):
    """Base class for all graph-related errors."""


class MissingCredentialsError(GraphError):
    """Raised when Neo4j connection details aren't configured."""
