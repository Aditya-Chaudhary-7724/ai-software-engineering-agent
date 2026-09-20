"""Composes GitHub repository cloning (this phase) with the EXISTING
Phase 1/2/3/5 pipeline — ingestion, parsing, indexing, and (optionally)
the knowledge graph — reused exactly as `backend/scripts/manual_agent_demo.py`
and this project's other manual demo scripts already do for a local
path. Nothing here reimplements ingestion, parsing, embedding, or graph
building; this module only wires a cloned repository's local path into
services that already exist.
"""

from dataclasses import dataclass
from typing import Optional

from ingestion.models import IngestionResult
from ingestion.service import IngestionService
from parsing.models import ParsingResult
from parsing.service import ParsingService
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.service import IndexingResult, IndexingService
from vectorstore.store import VectorStore

from graph.builder import GraphBuilder
from graph.client import Neo4jClient

from github_integration.models import ClonedRepository
from github_integration.service import GitHubIntegrationService


@dataclass(frozen=True)
class GitHubIngestionResult:
    cloned: ClonedRepository
    ingestion_result: IngestionResult
    parsing_result: ParsingResult
    index_result: IndexingResult


def ingest_github_repository(
    url: str,
    workspace_root: str,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    neo4j_client: Optional[Neo4jClient] = None,
    github_service: Optional[GitHubIntegrationService] = None,
) -> GitHubIngestionResult:
    """Clone -> ingest (Phase 1) -> parse (Phase 2) -> index (Phase 3).
    Also builds the Phase 5 knowledge graph if `neo4j_client` is given
    (optional, same as Phase 6's hybrid retrieval being an addition on
    top of base RAG rather than a hard requirement of it).
    """
    service = github_service or GitHubIntegrationService()
    cloned = service.clone_repository(url, workspace_root)

    ingestion_result = IngestionService().ingest(cloned.local_path)
    parsing_result = ParsingService().parse_repository(cloned.local_path, ingestion_result)
    index_result = IndexingService(embedding_provider, vector_store).index_repository(cloned.local_path)

    if neo4j_client is not None:
        GraphBuilder(neo4j_client).build(cloned.local_path, ingestion_result, parsing_result)

    return GitHubIngestionResult(
        cloned=cloned, ingestion_result=ingestion_result, parsing_result=parsing_result, index_result=index_result
    )
