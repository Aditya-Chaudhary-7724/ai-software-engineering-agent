"""Ask (frontend phase F4): read-only repository Q&A with grounded citations.

Uses the EXISTING `HybridRAGService.answer` unchanged (vector + keyword +
one-hop graph retrieval -> bounded context -> LLM). This endpoint only
adds what an HTTP surface needs:

- the repository's root path comes from the trusted id lookup, never the
  client; the request body forbids every field except `question`;
- honest labels: which LLM produced the text (and whether it is the
  placeholder `StubLLMProvider`), whether embeddings are semantic, and
  whether graph evidence was actually available — no scores are returned
  and citations are exactly the chunks the service put into the context;
- graph optionality: Neo4j is optional in this project. When it is not
  configured or not reachable, the same service runs with `_NoGraphClient`
  (no rows), i.e. genuinely vector + keyword retrieval, reported as such.

STRICTLY READ-ONLY: nothing here writes to the database or the
filesystem, and no agent, modification, sandbox, or GitHub code is
reachable from this module (enforced by tests/api/test_v1_ask.py).
It is a POST only so the question travels in the body, not in URLs/logs.
"""

import logging
import os
from typing import Any, List, Optional, Tuple

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError
from graph.queries import repository_exists
from hybrid.service import HybridRAGService
from jobs.ingestion import default_embedding_provider
from rag.llm.base import LLMProvider
from rag.llm.stub_provider import StubLLMProvider
from vectorstore.store import VectorStore

from api.dependencies import get_database_url, get_neo4j_client_factory, get_repository_record
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import AskAnswer, AskResponse, AskRetrieval, AskSource

logger = logging.getLogger("api.ask")

router = APIRouter(tags=["ask"])

MAX_QUESTION_CHARS = 2000


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)


class _NoGraphClient:
    """Stands in for Neo4jClient when the graph is unavailable: every query
    returns no rows, so graph expansion contributes nothing (rather than
    failing the whole answer). Never presented as graph evidence."""

    def run(self, query: str, **params: Any) -> list:
        return []

    def close(self) -> None:
        pass


def default_llm_provider() -> Tuple[LLMProvider, str]:
    if os.environ.get("LLM_API_KEY"):
        from rag.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(), "anthropic"
    return StubLLMProvider(), "stub"


def _open_graph(client_factory, root_path: str):
    """Returns (client, graph_status, graph_built)."""
    try:
        client = client_factory()
    except Neo4jMissingCredentialsError:
        return _NoGraphClient(), "not_configured", None
    try:
        built = repository_exists(client, root_path)
        return client, "used", built
    except Exception:
        client.close()
        return _NoGraphClient(), "unavailable", None


def _indexed_with_provider(database_url: str, repository_id: int) -> Optional[str]:
    """Embedding provider recorded by the repository's latest completed
    ingestion job (F3), if any — used only to warn about a mismatch."""
    try:
        conn = psycopg.connect(database_url, connect_timeout=5)
    except psycopg.OperationalError:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT progress->>'embedding_provider' FROM jobs WHERE repository_id = %s AND status = 'completed' "
                "ORDER BY finished_at DESC LIMIT 1",
                (repository_id,),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except psycopg.Error:
        return None
    finally:
        conn.close()


@router.post("/repositories/{repository_id}/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    record: RepositoryRecord = Depends(get_repository_record),
    database_url: str = Depends(get_database_url),
    neo4j_client_factory=Depends(get_neo4j_client_factory),
) -> AskResponse:
    question = request.question.strip()
    if not question:
        raise APIError(422, "invalid_question")

    embedding_provider, embedding_name = default_embedding_provider()
    llm_provider, llm_name = default_llm_provider()
    graph_client, graph_status, graph_built = _open_graph(neo4j_client_factory, record.root_path)

    def run(client) -> Any:
        service = HybridRAGService(VectorStore(database_url), client, embedding_provider, llm_provider)
        return service.answer(question, record.id, record.root_path)

    try:
        try:
            result = run(graph_client)
        except psycopg.Error:
            raise
        except Exception as exc:
            if graph_status != "used" or not _is_graph_error(exc):
                raise
            # Neo4j failed mid-request: answer again without graph evidence
            # (the LLM is only called after retrieval, so it is not paid twice).
            graph_status, graph_built = "unavailable", None
            result = run(_NoGraphClient())
    except psycopg.OperationalError:
        raise APIError(503, "database_unavailable")
    except psycopg.Error:
        logger.exception("ask failed in the database layer")
        raise APIError(503, "database_unavailable")
    except Exception:
        logger.exception("ask failed")
        raise APIError(502, "answer_generation_failed")
    finally:
        graph_client.close()

    warnings: List[str] = []
    if result.context_chunk_count == 0:
        warnings.append("no_relevant_context")
    indexed_with = _indexed_with_provider(database_url, record.id)
    if indexed_with and indexed_with != embedding_name:
        warnings.append("embedding_provider_mismatch")

    return AskResponse(
        repository_id=record.id,
        question=question,
        answer=AskAnswer(text=result.answer, generator=llm_name, is_placeholder=llm_name == "stub"),
        retrieval=AskRetrieval(
            embedding_provider=embedding_name,
            semantic_embeddings=embedding_name != "deterministic_local",
            graph=graph_status,
            graph_built=graph_built,
        ),
        sources=[
            AskSource(
                relative_path=s.relative_path,
                start_line=s.start_line,
                end_line=s.end_line,
                chunk_type=s.chunk_type,
                symbol_name=s.symbol_name,
                found_via=list(s.found_via),
            )
            for s in result.sources
        ],
        context_chunk_count=result.context_chunk_count,
        warnings=warnings,
    )


def _is_graph_error(exc: Exception) -> bool:
    module = type(exc).__module__ or ""
    return module.startswith("neo4j")
