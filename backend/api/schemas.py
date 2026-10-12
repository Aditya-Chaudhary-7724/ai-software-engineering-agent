"""Response models for `/api/v1`.

Every route declares one of these as its `response_model`, which makes
them an OUTPUT ALLOWLIST: FastAPI serializes only the fields declared
here. None of them has a field for a server filesystem path, so an
internal object carrying one (e.g. `RepositoryRecord.root_path`) cannot
leak through a response even by mistake.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class DependencyCapability(BaseModel):
    ok: bool
    detail: str


class Capabilities(BaseModel):
    api_version: str
    database: DependencyCapability
    graph: DependencyCapability
    # Whether a real provider credential is configured — booleans only,
    # never a value. Without them the backend uses StubLLMProvider /
    # DeterministicLocalEmbeddingProvider (see backend/config.py).
    llm_provider_configured: bool
    embedding_provider_configured: bool
    github_token_configured: bool
    sandbox_docker_available: bool
    sandbox_image_available: bool
    # Which operations this API version actually exposes. Anything false
    # here has no endpoint yet — the UI must not imply otherwise.
    features: Dict[str, bool]


class RepositorySummary(BaseModel):
    id: int
    name: str
    first_ingested_at: datetime
    chunk_count: int
    indexed_file_count: int
    languages: List[str]
    available_on_disk: bool
    # `owner/repo` when the checkout lives in the ingestion workspace.
    github_full_name: Optional[str] = None


class RepositoryList(BaseModel):
    repositories: List[RepositorySummary]
    total: int
    limit: int
    offset: int


class RepositoryDetail(RepositorySummary):
    chunks_by_language: Dict[str, int]
    chunks_by_type: Dict[str, int]


class FileEntry(BaseModel):
    relative_path: str
    language: Optional[str]
    category: str
    size_bytes: int


class FileTree(BaseModel):
    repository_id: int
    directory: str
    files: List[FileEntry]
    total: int
    truncated: bool


class FileContent(BaseModel):
    repository_id: int
    relative_path: str
    language: Optional[str]
    size_bytes: int
    truncated: bool
    content: str


class GraphSummary(BaseModel):
    repository_id: int
    built: bool
    folders: int
    files: int
    classes: int
    functions: int


class TraceSummary(BaseModel):
    trace_id: str
    name: str
    status: str
    started_at: str
    ended_at: Optional[str]
    duration_ms: Optional[float]
    repository_id: Optional[int]
    question: Optional[str]
    span_count: int
    error_span_count: int


class TraceList(BaseModel):
    traces: List[TraceSummary]
    total: int
    limit: int
    offset: int


class SpanEvent(BaseModel):
    timestamp: str
    name: str
    attributes: Dict[str, Any]


class Span(BaseModel):
    span_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str
    status: str
    start_time: str
    end_time: Optional[str]
    duration_ms: Optional[float]
    attributes: Dict[str, Any]
    error: Optional[str]
    events: List[SpanEvent]


class TraceDetail(TraceSummary):
    attributes: Dict[str, Any]
    spans: List[Span]


class Job(BaseModel):
    """A background job as seen by the client. There is deliberately no
    field for the server-side checkout path the job writes to."""

    id: str
    job_type: str
    status: str
    stage: str
    github_full_name: Optional[str]
    repository_id: Optional[int]
    build_graph: bool
    graph_status: str
    progress: Dict[str, Any]
    error_code: Optional[str]
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class JobSubmission(BaseModel):
    job: Job
    # False when an identical job was already queued/running: the client
    # gets that job back instead of a duplicate being created.
    created: bool


class JobList(BaseModel):
    jobs: List[Job]


class AskSource(BaseModel):
    """One citation: a chunk the answer's context was actually built from.
    No relevance score is exposed — the ranking weights are heuristics, and
    with non-semantic embeddings a number would imply precision that isn't
    there. `found_via` says HOW it was retrieved (vector/keyword/graph:*)."""

    relative_path: str
    start_line: int
    end_line: int
    chunk_type: str
    symbol_name: Optional[str]
    found_via: List[str]


class AskAnswer(BaseModel):
    text: str
    generator: str  # "anthropic" | "stub"
    # True for StubLLMProvider: deterministic placeholder text, not an answer.
    is_placeholder: bool


class AskRetrieval(BaseModel):
    embedding_provider: str  # "openai" | "deterministic_local"
    semantic_embeddings: bool
    graph: str  # "used" | "not_configured" | "unavailable"
    graph_built: Optional[bool]


class AskResponse(BaseModel):
    repository_id: int
    question: str
    answer: AskAnswer
    retrieval: AskRetrieval
    sources: List[AskSource]
    context_chunk_count: int
    # "no_relevant_context" | "embedding_provider_mismatch"
    warnings: List[str]


class GraphNode(BaseModel):
    """A knowledge-graph node as returned to clients. Only these fields
    exist — `root_path` / `repository_root_path` (server paths) and other
    stored properties are never serialized (api/routes/graph.py allowlist)."""

    id: str
    kind: str  # Repository | Folder | File | Class | Function | Module
    label: str
    relative_path: Optional[str] = None
    qualified_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    language: Optional[str] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str  # CONTAINS | DEFINES | IMPORTS | RESOLVES_TO | INHERITS


class GraphNeighborhood(BaseModel):
    repository_id: int
    focus: GraphNode
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    total_neighbors: int
    truncated: bool
    limit: int
    relationship_types: List[str]


class GraphSearchResults(BaseModel):
    repository_id: int
    query: str
    results: List[GraphNode]
    limit: int
