"""search_code (Phase 3+4 vector+keyword retrieval) and search_symbol
(Phase 5 exact graph lookup) as formal tools.

Both need a live service (a Postgres connection / a Neo4j client), so
each is built via a factory closing over that service — the same
pattern agent/nodes.py uses, for the same reason (tool handlers only
receive validated input, not arbitrary dependencies).
"""

from typing import Callable

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.keyword_search import keyword_search
from rag.ranking import merge_and_rank

from graph.queries import find_symbol

from tools.schemas import SearchCodeHit, SearchCodeInput, SearchCodeOutput, SearchSymbolInput, SearchSymbolOutput, SymbolMatch


def make_search_code(
    vector_store: VectorStore, embedding_provider: EmbeddingProvider
) -> Callable[[SearchCodeInput], SearchCodeOutput]:
    def search_code(input_data: SearchCodeInput) -> SearchCodeOutput:
        conn = vector_store.connect()
        try:
            query_embedding = embedding_provider.embed([input_data.query])[0]
            vector_hits = vector_store.similarity_search(
                conn,
                query_embedding,
                top_k=input_data.top_k,
                repository_id=input_data.repository_id,
                language=input_data.language,
            )
            keyword_hits = keyword_search(
                conn,
                input_data.query,
                top_k=input_data.top_k,
                repository_id=input_data.repository_id,
                language=input_data.language,
            )
        finally:
            conn.close()

        ranked = merge_and_rank(vector_hits, keyword_hits, top_k=input_data.top_k)

        found_via_by_id: dict[int, list[str]] = {}
        for vector_hit in vector_hits:
            found_via_by_id.setdefault(vector_hit.chunk_id, []).append("vector")
        for keyword_hit in keyword_hits:
            found_via_by_id.setdefault(keyword_hit.chunk_id, []).append("keyword")

        hits = [
            SearchCodeHit(
                relative_path=c.relative_path,
                chunk_type=c.chunk_type,
                symbol_name=c.symbol_name,
                start_line=c.start_line,
                end_line=c.end_line,
                content=c.content,
                found_via=found_via_by_id.get(c.chunk_id, []),
            )
            for c in ranked
        ]
        return SearchCodeOutput(hits=hits)

    return search_code


def make_search_symbol(neo4j_client: Neo4jClient) -> Callable[[SearchSymbolInput], SearchSymbolOutput]:
    def search_symbol(input_data: SearchSymbolInput) -> SearchSymbolOutput:
        rows = find_symbol(neo4j_client, input_data.root_path, input_data.name)
        matches = [SymbolMatch(kind=r["kind"], relative_path=r["relative_path"], name=r["name"]) for r in rows]
        return SearchSymbolOutput(matches=matches)

    return search_symbol
