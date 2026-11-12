"""Executes ingestion and re-index jobs.

Composes the EXISTING services — nothing here re-implements cloning,
ingestion, parsing, chunking, embedding, or graph building:

    ingest_github:  GitHubIntegrationService.get_repository_metadata (size check)
                    -> GitHubIntegrationService.clone_repository (URL validation +
                       workspace confinement + clone, Phase 11/14)
                    -> IndexingService.index_repository (Phases 1-3; atomic
                       replace, sensitive-file exclusion, F3 fix)
                    -> [optional] IngestionService + ParsingService + GraphBuilder
    reindex:        the same indexing/graph steps against the repository's
                    existing server-side checkout (no network access).

This mirrors `github_integration/pipeline.py`'s composition, adding what a
background job needs that the pipeline doesn't have: per-stage status, a
repository size limit, cleanup of a failed clone, and graph building whose
failure is reported separately instead of discarding a completed index.

Repository content stays untrusted data throughout: it is only cloned
(git does not run repository hooks on clone), filtered, parsed, and
embedded — never executed.
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import psycopg
import requests

from github_integration.exceptions import (
    GitHubAPIError,
    GitOperationError,
    InvalidRepositoryURLError,
    WorkspaceError,
)
from github_integration.service import GitHubIntegrationService
from github_integration.url_validation import parse_github_url
from github_integration.workspace import resolve_clone_destination
from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError
from graph.schema import apply_constraints
from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.factory import configured_embedding_provider
from vectorstore.exceptions import EmbeddingConfigurationError, SchemaOutOfDateError
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from jobs.models import (
    GRAPH_COMPLETED,
    GRAPH_FAILED,
    GRAPH_NOT_REQUESTED,
    GRAPH_NOT_RUN,
    JOB_TYPE_INGEST_GITHUB,
    JOB_TYPE_REINDEX,
    STAGE_BUILDING_GRAPH,
    STAGE_CLONING,
    STAGE_FETCHING_METADATA,
    STAGE_INDEXING,
    Job,
    JobFailure,
)
from jobs.store import JobStore

logger = logging.getLogger("jobs.ingestion")

DEFAULT_MAX_REPOSITORY_KB = 250_000  # GitHub-reported size; ~250 MB


def default_embedding_provider() -> Tuple[EmbeddingProvider, str]:
    """The configured embedding provider (vectorstore/embeddings/factory.py;
    EMBEDDING_PROVIDER, or the original OpenAI-if-key-else-hash rule when
    unset). The name is recorded on the job so the UI never implies semantic
    search quality the index doesn't have. Kept under this name because the
    Ask route and the agent executor import it."""
    return configured_embedding_provider()


def canonical_github_reference(url: str):
    """Validates with the existing `parse_github_url` (raises
    InvalidRepositoryURLError), then lower-cases owner/repo: GitHub names are
    case-insensitive, so this gives each repository exactly one workspace
    directory and one de-duplication key regardless of how the URL was typed."""
    reference = parse_github_url(url)
    return parse_github_url(f"https://github.com/{reference.owner.lower()}/{reference.repo.lower()}")


def find_repository_id(database_url: str, root_path: str) -> Optional[int]:
    conn = psycopg.connect(database_url, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM repositories WHERE root_path = %s", (root_path,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def github_full_name_for(root_path: str, workspace_root: Optional[str]) -> Optional[str]:
    """`owner/repo` if `root_path` is a checkout inside the ingestion
    workspace, else None. Derived, so no schema change is needed."""
    if not workspace_root:
        return None
    try:
        relative = Path(root_path).resolve().relative_to(Path(workspace_root).resolve())
    except (ValueError, OSError):
        return None
    return "/".join(relative.parts) if len(relative.parts) == 2 else None


@dataclass
class IngestionDependencies:
    database_url: str
    workspace_root: str
    max_repository_kb: int = DEFAULT_MAX_REPOSITORY_KB
    github_service_factory: Callable[[], GitHubIntegrationService] = GitHubIntegrationService
    embedding_provider_factory: Callable[[], Tuple[EmbeddingProvider, str]] = default_embedding_provider
    neo4j_client_factory: Callable[[], Neo4jClient] = Neo4jClient


def _github_api_error_code(exc: GitHubAPIError) -> str:
    message = str(exc)
    if "was not found" in message:
        return "github_repository_not_found"
    if " 403 " in message or " 429 " in message:
        return "github_rate_limited"
    return "github_api_error"


class IngestionExecutor:
    def __init__(self, store: JobStore, deps: IngestionDependencies) -> None:
        self._store = store
        self._deps = deps

    # -- entry point -----------------------------------------------------

    def execute(self, job: Job) -> None:
        """Runs one claimed job to a terminal state. Never raises: every
        outcome is recorded on the job, and only AFTER the underlying work
        has finished — a job is never reported completed early."""
        try:
            if job.job_type == JOB_TYPE_INGEST_GITHUB:
                repository_id, progress = self._ingest_github(job)
            elif job.job_type == JOB_TYPE_REINDEX:
                repository_id, progress = self._reindex(job)
            else:
                raise JobFailure("unsupported_job_type")
            graph_status, graph_progress = self._build_graph_if_requested(job)
            self._store.complete(job.id, repository_id=repository_id, graph_status=graph_status, progress={**progress, **graph_progress})
        except EmbeddingConfigurationError:
            logger.warning("job refused: the embedding provider is misconfigured", extra={"job_id": job.id})
            self._store.fail(job.id, "embedding_not_configured", GRAPH_NOT_RUN if job.build_graph else None)
        except JobFailure as failure:
            logger.warning("job failed", extra={"job_id": job.id, "error_code": failure.code})
            self._store.fail(job.id, failure.code, GRAPH_NOT_RUN if job.build_graph else None)
        except Exception:
            logger.exception("job crashed", extra={"job_id": job.id})
            self._store.fail(job.id, "internal_error", GRAPH_NOT_RUN if job.build_graph else None)

    # -- job types -------------------------------------------------------

    def _ingest_github(self, job: Job) -> Tuple[int, Dict[str, Any]]:
        workspace_root = self._deps.workspace_root
        try:
            reference = canonical_github_reference(f"https://github.com/{job.github_full_name}")
            destination = resolve_clone_destination(workspace_root, reference)
        except (InvalidRepositoryURLError, WorkspaceError):
            raise JobFailure("invalid_github_url")
        # The stored target must be exactly what the server computes from
        # the validated URL — never a path that came from anywhere else.
        if str(destination) != job.target_path:
            raise JobFailure("workspace_error")

        if find_repository_id(self._deps.database_url, str(destination)) is not None:
            raise JobFailure("repository_already_ingested")
        if destination.exists():
            # Left behind by an earlier failed or interrupted job (the
            # one-active-job-per-target index rules out a concurrent one).
            self._remove_owned_checkout(destination)

        service = self._deps.github_service_factory()
        self._store.update_stage(job.id, STAGE_FETCHING_METADATA)
        metadata = self._github_call(lambda: service.get_repository_metadata(reference.url))
        if metadata.size_kb is not None and metadata.size_kb > self._deps.max_repository_kb:
            raise JobFailure("repository_too_large")

        self._store.update_stage(job.id, STAGE_CLONING, {"default_branch": metadata.default_branch})
        try:
            cloned = self._github_call(lambda: service.clone_repository(reference.url, workspace_root))
        except JobFailure:
            self._remove_owned_checkout(destination)  # a failed clone can leave a partial directory
            raise
        if cloned.local_path != str(destination):  # defense in depth; both come from resolve_clone_destination
            self._remove_owned_checkout(Path(cloned.local_path))
            raise JobFailure("workspace_error")

        try:
            return self._index(job, destination)
        except JobFailure:
            # Indexing is one transaction: on failure no repository row was
            # written, so the fresh checkout is orphaned — remove it so a
            # retry starts clean.
            self._remove_owned_checkout(destination)
            raise

    def _reindex(self, job: Job) -> Tuple[int, Dict[str, Any]]:
        root = Path(job.target_path)
        if not root.is_dir():
            raise JobFailure("repository_files_unavailable")
        return self._index(job, root)

    # -- steps -----------------------------------------------------------

    def _github_call(self, call):
        try:
            return call()
        except InvalidRepositoryURLError:
            raise JobFailure("invalid_github_url")
        except WorkspaceError:
            raise JobFailure("workspace_error")
        except GitHubAPIError as exc:
            raise JobFailure(_github_api_error_code(exc))
        except GitOperationError:
            raise JobFailure("clone_failed")
        except requests.RequestException:
            raise JobFailure("github_unreachable")

    def _index(self, job: Job, root: Path) -> Tuple[int, Dict[str, Any]]:
        self._store.update_stage(job.id, STAGE_INDEXING)
        embedding_provider, provider_name = self._deps.embedding_provider_factory()
        try:
            result = IndexingService(embedding_provider, VectorStore(self._deps.database_url)).index_repository(str(root))
        except psycopg.OperationalError:
            raise JobFailure("database_unavailable")
        except SchemaOutOfDateError:
            # Indexing never applies the schema itself; the operator must run
            # backend/scripts/apply_schema.py.
            logger.warning("job refused: database schema out of date (run apply_schema.py)", extra={"job_id": job.id})
            raise JobFailure("schema_out_of_date")
        except Exception:
            logger.exception("indexing failed", extra={"job_id": job.id})
            raise JobFailure("indexing_failed")
        return result.repository_id, {"chunks_indexed": result.chunks_indexed, "embedding_provider": provider_name}

    def _build_graph_if_requested(self, job: Job) -> Tuple[str, Dict[str, Any]]:
        if not job.build_graph:
            return GRAPH_NOT_REQUESTED, {}
        root = job.target_path
        self._store.update_stage(job.id, STAGE_BUILDING_GRAPH)
        try:
            client = self._deps.neo4j_client_factory()
        except Neo4jMissingCredentialsError:
            return GRAPH_FAILED, {"graph_error_code": "graph_not_configured"}
        try:
            apply_constraints(client)
            ingestion = IngestionService().ingest(root)
            parsed = ParsingService().parse_repository(root, ingestion)
            built = GraphBuilder(client).build(root, ingestion, parsed)
            return GRAPH_COMPLETED, {"graph_node_counts": dict(built.node_counts)}
        except Exception:
            # The index is already committed; a graph failure is reported
            # alongside it rather than throwing that work away.
            logger.exception("graph build failed", extra={"job_id": job.id})
            return GRAPH_FAILED, {"graph_error_code": "graph_build_failed"}
        finally:
            client.close()

    def _remove_owned_checkout(self, path: Path) -> None:
        """Deletes a checkout ONLY if it is exactly <workspace>/<owner>/<repo>
        and not a symlink — never anything else on the server."""
        workspace = Path(self._deps.workspace_root).resolve()
        try:
            relative = path.resolve().relative_to(workspace)
        except (ValueError, OSError):
            return
        if len(relative.parts) == 2 and not path.is_symlink() and path.is_dir():
            shutil.rmtree(path)
