"""PostgreSQL + pgvector storage and similarity search.

No ORM: the schema is two tables and the queries are simple enough
that raw, parameterized SQL via psycopg is clearer than an ORM layer
would be, and avoids an unjustified dependency.
"""

from typing import Optional

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql

from vectorstore.exceptions import EmbeddingDimensionError, ReindexRequiredError
from vectorstore.models import EMBEDDING_COLUMNS, CodeChunk, SearchResult


def embedding_column(dimension: int) -> str:
    """The vector column for `dimension`, from the fixed allowlist only."""
    column = EMBEDDING_COLUMNS.get(dimension)
    if column is None:
        supported = ", ".join(str(d) for d in sorted(EMBEDDING_COLUMNS))
        raise EmbeddingDimensionError(f"unsupported embedding dimension {dimension} (supported: {supported})")
    return column


class VectorStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def connect(self, *, register_vector_type: bool = True) -> psycopg.Connection:
        """`register_vector_type=False` is for schema checks only: pgvector's
        registration fails on a database where the schema (and so the
        `vector` extension) was never applied."""
        conn = psycopg.connect(self._database_url)
        if register_vector_type:
            register_vector(conn)
        return conn

    def get_or_create_repository(
        self,
        conn: psycopg.Connection,
        name: str,
        root_path: str,
        *,
        embedding_model: Optional[str] = None,
        commit: bool = True,
    ) -> int:
        """Upserts by `root_path`. The upsert also row-locks the repository
        until the surrounding transaction ends, which is what serializes
        concurrent re-indexes of the same repository (see IndexingService).
        `commit=False` lets a caller make this part of a larger transaction.

        `embedding_model` records which model produced the repository's
        vectors; IndexingService always passes it, in the same transaction
        that replaces the chunks. Omitted, an existing value is kept.
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repositories (name, root_path, embedding_model)
                VALUES (%(name)s, %(root_path)s, %(embedding_model)s)
                ON CONFLICT (root_path) DO UPDATE SET
                    name = EXCLUDED.name,
                    embedding_model = COALESCE(EXCLUDED.embedding_model, repositories.embedding_model)
                RETURNING id
                """,
                {"name": name, "root_path": root_path, "embedding_model": embedding_model},
            )
            row = cur.fetchone()
        if commit:
            conn.commit()
        assert row is not None
        return row[0]

    def delete_repository_chunks(self, conn: psycopg.Connection, repository_id: int) -> int:
        """Deletes every chunk of exactly one repository. Never commits —
        always used inside a caller's transaction, so the deletion only
        becomes visible together with the replacement chunks."""
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %(repository_id)s", {"repository_id": repository_id})
            return cur.rowcount

    def insert_chunks(
        self,
        conn: psycopg.Connection,
        repository_id: int,
        chunks: list[CodeChunk],
        embeddings: list[list[float]],
        *,
        commit: bool = True,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        dimensions = {len(embedding) for embedding in embeddings}
        if len(dimensions) > 1:
            raise EmbeddingDimensionError(f"mixed embedding dimensions in one batch: {sorted(dimensions)}")
        if not embeddings:
            if commit:
                conn.commit()
            return
        column = embedding_column(dimensions.pop())
        statement = sql.SQL(
            """
            INSERT INTO code_chunks (
                repository_id, relative_path, language, chunk_type,
                symbol_name, qualified_name, start_line, end_line,
                content, {column}
            ) VALUES (
                %(repository_id)s, %(relative_path)s, %(language)s, %(chunk_type)s,
                %(symbol_name)s, %(qualified_name)s, %(start_line)s, %(end_line)s,
                %(content)s, %(embedding)s
            )
            """
        ).format(column=sql.Identifier(column))

        with conn.cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings):
                cur.execute(
                    statement,
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
        if commit:
            conn.commit()

    def repository_embedding_model(self, conn: psycopg.Connection, repository_id: int) -> tuple:
        """(exists, embedding_model) for one repository."""
        with conn.cursor() as cur:
            cur.execute("SELECT embedding_model FROM repositories WHERE id = %s", (repository_id,))
            row = cur.fetchone()
        return (row is not None, row[0] if row else None)

    def similarity_search(
        self,
        conn: psycopg.Connection,
        query_embedding: list[float],
        top_k: int = 5,
        repository_id: Optional[int] = None,
        language: Optional[str] = None,
        chunk_type: Optional[str] = None,
        *,
        embedding_model: str,
    ) -> list[SearchResult]:
        """Nearest chunks to `query_embedding`, which `embedding_model` produced.

        Never compares vectors from different models (regression: earlier
        searches embedded the query with whatever provider was configured
        NOW and silently ranked vectors another model had produced):
        - for one repository, a different or unrecorded model raises
          ReindexRequiredError;
        - across repositories, only repositories indexed with that model
          are searched.
        """
        column = embedding_column(len(query_embedding))
        conditions: list[sql.Composable] = [sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(column))]
        params: dict = {"query_embedding": query_embedding, "top_k": top_k, "embedding_model": embedding_model}

        if repository_id is not None:
            exists, indexed_with = self.repository_embedding_model(conn, repository_id)
            if not exists:
                return []
            if indexed_with != embedding_model:
                raise ReindexRequiredError(repository_id, indexed_with, embedding_model)
            conditions.append(sql.SQL("c.repository_id = %(repository_id)s"))
            params["repository_id"] = repository_id
        if language is not None:
            conditions.append(sql.SQL("c.language = %(language)s"))
            params["language"] = language
        if chunk_type is not None:
            conditions.append(sql.SQL("c.chunk_type = %(chunk_type)s"))
            params["chunk_type"] = chunk_type

        query = sql.SQL(
            """
            SELECT c.id, c.relative_path, c.language, c.chunk_type, c.symbol_name, c.qualified_name,
                   c.start_line, c.end_line, c.content,
                   c.{column} <=> %(query_embedding)s::vector AS distance
            FROM code_chunks c
            JOIN repositories r ON r.id = c.repository_id AND r.embedding_model = %(embedding_model)s
            WHERE {conditions}
            ORDER BY c.{column} <=> %(query_embedding)s::vector
            LIMIT %(top_k)s
            """
        ).format(column=sql.Identifier(column), conditions=sql.SQL(" AND ").join(conditions))

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
