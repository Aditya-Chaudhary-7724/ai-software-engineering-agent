"""Keyword/exact search over code_chunks using PostgreSQL full-text search.

Complements Phase 3's vector search: semantically vague queries win on
vector similarity, but exact identifiers/strings (a function name, an
error message) often win on keyword match instead. `ranking.py` merges
the two rather than picking one.

Query terms are OR'd together (`to_tsquery('english', 'term1 | term2 | ...')`)
rather than AND'd (which `plainto_tsquery` would do): a natural-language
question like "Where is authentication implemented?" should match a
chunk containing "authenticate" even though it doesn't also contain
"implement" — requiring every word to match is too strict for this
use case. This is a keyword *recall* aid for ranking.py to combine
with vector similarity, not a precise phrase search.
"""

import re
from typing import Optional

import psycopg

from rag.models import Candidate

_WORD_PATTERN = re.compile(r"\w+")


def _build_or_query(text: str) -> str:
    """Extract word tokens and OR them for to_tsquery.

    Falls back to an empty tsquery (matches nothing, never errors) if
    the input has no word characters at all — safer than passing
    arbitrary punctuation through to to_tsquery's expression parser.
    """
    words = _WORD_PATTERN.findall(text)
    return " | ".join(words) if words else ""


def keyword_search(
    conn: psycopg.Connection,
    query: str,
    top_k: int = 10,
    repository_id: Optional[int] = None,
    language: Optional[str] = None,
    chunk_type: Optional[str] = None,
) -> list[Candidate]:
    tsquery = _build_or_query(query)
    conditions = ["to_tsvector('english', content) @@ to_tsquery('english', %(tsquery)s)"]
    params: dict = {"tsquery": tsquery, "top_k": top_k}

    if repository_id is not None:
        conditions.append("repository_id = %(repository_id)s")
        params["repository_id"] = repository_id
    if language is not None:
        conditions.append("language = %(language)s")
        params["language"] = language
    if chunk_type is not None:
        conditions.append("chunk_type = %(chunk_type)s")
        params["chunk_type"] = chunk_type

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT id, relative_path, language, chunk_type, symbol_name, qualified_name,
               start_line, end_line, content,
               ts_rank(to_tsvector('english', content), to_tsquery('english', %(tsquery)s)) AS rank
        FROM code_chunks
        WHERE {where_clause}
        ORDER BY rank DESC
        LIMIT %(top_k)s
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        Candidate(
            chunk_id=row[0],
            relative_path=row[1],
            language=row[2],
            chunk_type=row[3],
            symbol_name=row[4],
            qualified_name=row[5],
            start_line=row[6],
            end_line=row[7],
            content=row[8],
            vector_similarity=0.0,
            keyword_score=row[9],
            combined_score=0.0,  # filled in by ranking.merge_and_rank
        )
        for row in rows
    ]
