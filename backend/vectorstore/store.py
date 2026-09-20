"""PostgreSQL + pgvector storage and similarity search.

No ORM: the schema is two tables and the queries are simple enough
that raw, parameterized SQL via psycopg is clearer than an ORM layer
would be, and avoids an unjustified dependency.
"""

from typing import Optional

import psycopg
from pgvector.psycopg import register_vector

from vectorstore.models import CodeChunk, SearchResult


class VectorStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self._database_url)
        register_vector(conn)
        return conn

    def get_or_create_repository(self, conn: psycopg.Connection, name: str, root_path: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repositories (name, root_path)
                VALUES (%(name)s, %(root_path)s)
                ON CONFLICT (root_path) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                {"name": name, "root_path": root_path},
            )
            row = cur.fetchone()
        conn.commit()
        assert row is not None
        return row[0]

    def insert_chunks(
        self,
        conn: psycopg.Connection,
        repository_id: int,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")

        with conn.cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings):
                cur.execute(
                    """
                    INSERT INTO code_chunks (
                        repository_id, relative_path, language, chunk_type,
                        symbol_name, qualified_name, start_line, end_line,
                        content, embedding
                    ) VALUES (
                        %(repository_id)s, %(relative_path)s, %(language)s, %(chunk_type)s,
                        %(symbol_name)s, %(qualified_name)s, %(start_line)s, %(end_line)s,
                        %(content)s, %(embedding)s
                    )
                    """,
                    {
                        "repository_id": repository_id,
                        "relative_path": chunk.relative_path,
                        "language": chunk.language,
                        "chunk_type": chunk.chunk_type,
                        "symbol_name": chunk.symbol_name,
                        "qualified_name": chunk.qualified_name,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content": chunk.content,
                        "embedding": embedding,
                    },
                )
        conn.commit()

    def similarity_search(
        self,
        conn: psycopg.Connection,
        query_embedding: list[float],
        top_k: int = 5,
        repository_id: Optional[int] = None,
        language: Optional[str] = None,
        chunk_type: Optional[str] = None,
    ) -> list[SearchResult]:
        conditions = []
        params: dict = {"query_embedding": query_embedding, "top_k": top_k}

        if repository_id is not None:
            conditions.append("repository_id = %(repository_id)s")
            params["repository_id"] = repository_id
        if language is not None:
            conditions.append("language = %(language)s")
            params["language"] = language
        if chunk_type is not None:
            conditions.append("chunk_type = %(chunk_type)s")
            params["chunk_type"] = chunk_type

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT id, relative_path, language, chunk_type, symbol_name, qualified_name,
                   start_line, end_line, content,
                   embedding <=> %(query_embedding)s::vector AS distance
            FROM code_chunks
            {where_clause}
            ORDER BY embedding <=> %(query_embedding)s::vector
            LIMIT %(top_k)s
        """

        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        return [
            SearchResult(
                chunk_id=row[0],
                relative_path=row[1],
                language=row[2],
                chunk_type=row[3],
                symbol_name=row[4],
                qualified_name=row[5],
                start_line=row[6],
                end_line=row[7],
                content=row[8],
                distance=row[9],
            )
            for row in rows
        ]
