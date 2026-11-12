"""Pre-generated demo examples (read-only).

Served to the operator and to the public demo (allowlisted in
api/demo_scope.py, for demo repositories only). A plain database read: no
LLM call, no demo quota, no rate limit. Every item says it is
pre-generated and which model produced it.
"""

import psycopg
from fastapi import APIRouter, Depends

from api.dependencies import get_database_url, get_repository_record
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import DemoExample, DemoExampleList, DemoExampleSource
from demo.examples import DemoExampleStore

router = APIRouter(tags=["demo-examples"])


@router.get("/repositories/{repository_id}/demo-examples", response_model=DemoExampleList)
def list_demo_examples(
    record: RepositoryRecord = Depends(get_repository_record),
    database_url: str = Depends(get_database_url),
) -> DemoExampleList:
    try:
        examples = DemoExampleStore(database_url).list_for_repository(record.id)
    except psycopg.errors.UndefinedTable:
        raise APIError(503, "schema_out_of_date")
    except psycopg.OperationalError:
        raise APIError(503, "database_unavailable")
    return DemoExampleList(
        repository_id=record.id,
        examples=[
            DemoExample(
                kind=e.kind,
                pre_generated=True,
                prompt=e.prompt,
                answer=e.answer,
                sources=[
                    DemoExampleSource(
                        relative_path=str(s.get("relative_path", "")),
                        start_line=s.get("start_line") if isinstance(s.get("start_line"), int) else None,
                        end_line=s.get("end_line") if isinstance(s.get("end_line"), int) else None,
                        symbol_name=s.get("symbol_name") if isinstance(s.get("symbol_name"), str) else None,
                    )
                    for s in e.sources
                    if isinstance(s, dict) and isinstance(s.get("relative_path"), str) and not s["relative_path"].startswith("/")
                ],
                diff=e.diff,
                diff_relative_path=e.diff_relative_path,
                generator=e.generator,
                generated_at=e.generated_at,
            )
            for e in examples
        ],
    )
