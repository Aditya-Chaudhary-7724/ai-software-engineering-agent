"""Repository list/detail (read-only). Responses never include the
server-side `root_path` — only whether the files are still present."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from api import repository_catalog
from api.demo_scope import demo_repository_filter
from api.dependencies import get_database_url, get_repository_record
from api.repository_catalog import RepositoryRecord
from api.schemas import RepositoryDetail, RepositoryList, RepositorySummary
from jobs.ingestion import github_full_name_for

router = APIRouter(tags=["repositories"])


def _summary(record: RepositoryRecord) -> RepositorySummary:
    return RepositorySummary(
        id=record.id,
        name=record.name,
        first_ingested_at=record.first_ingested_at,
        chunk_count=record.chunk_count,
        indexed_file_count=record.indexed_file_count,
        languages=record.languages,
        available_on_disk=Path(record.root_path).is_dir(),
        github_full_name=github_full_name_for(record.root_path, os.environ.get("WORKSPACE_ROOT")),
    )


@router.get("/repositories", response_model=RepositoryList)
def list_repositories(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    database_url: str = Depends(get_database_url),
    only_ids=Depends(demo_repository_filter),
) -> RepositoryList:
    records, total = repository_catalog.list_repositories(database_url, limit, offset, only_ids)
    return RepositoryList(repositories=[_summary(r) for r in records], total=total, limit=limit, offset=offset)


@router.get("/repositories/{repository_id}", response_model=RepositoryDetail)
def get_repository(record: RepositoryRecord = Depends(get_repository_record)) -> RepositoryDetail:
    return RepositoryDetail(
        **_summary(record).model_dump(),
        chunks_by_language=record.chunks_by_language,
        chunks_by_type=record.chunks_by_type,
    )
