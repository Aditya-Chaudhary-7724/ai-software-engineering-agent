"""Pre-generated public-demo examples: storage (schema version 4).

Examples are produced once, by the operator, with
backend/scripts/generate_demo_examples.py, and served to demo visitors
instantly, labelled "pre-generated example". Reading them costs no LLM call
and no demo quota. A repository's examples are always replaced as a whole,
in one transaction, so a visitor never sees a half-updated set.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.types.json import Jsonb

KIND_ASK = "ask"
KIND_TASK = "task"
MAX_ASK_EXAMPLES = 5


@dataclass(frozen=True)
class DemoExample:
    kind: str
    position: int
    prompt: str
    generator: str
    answer: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)
    diff: Optional[str] = None
    diff_relative_path: Optional[str] = None
    generated_at: Optional[datetime] = None


class DemoExampleStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def replace_for_repository(self, repository_id: int, examples: List[DemoExample]) -> None:
        with psycopg.connect(self._database_url) as conn:
            with conn.transaction():
                conn.execute("DELETE FROM demo_examples WHERE repository_id = %s", (repository_id,))
                for ex in examples:
                    conn.execute(
                        "INSERT INTO demo_examples (repository_id, kind, position, prompt, answer, sources, diff, "
                        "diff_relative_path, generator) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (repository_id, ex.kind, ex.position, ex.prompt, ex.answer, Jsonb(ex.sources), ex.diff,
                         ex.diff_relative_path, ex.generator),
                    )

    def count_for_repository(self, repository_id: int) -> int:
        with psycopg.connect(self._database_url) as conn:
            row = conn.execute("SELECT count(*) FROM demo_examples WHERE repository_id = %s", (repository_id,)).fetchone()
        return int(row[0]) if row else 0

    def list_for_repository(self, repository_id: int) -> List[DemoExample]:
        with psycopg.connect(self._database_url) as conn:
            rows = conn.execute(
                "SELECT kind, position, prompt, generator, answer, sources, diff, diff_relative_path, generated_at "
                "FROM demo_examples WHERE repository_id = %s ORDER BY kind, position",
                (repository_id,),
            ).fetchall()
        return [
            DemoExample(kind=r[0], position=r[1], prompt=r[2], generator=r[3], answer=r[4], sources=list(r[5] or []),
                        diff=r[6], diff_relative_path=r[7], generated_at=r[8])
            for r in rows
        ]
