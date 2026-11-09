"""LangGraph checkpointers for the agent (frontend phase F6).

WHY POSTGRES: the human-approval pause is a LangGraph `interrupt()`, and
its state lives in the checkpointer. The default `MemorySaver` keeps it in
one process's memory — an API restart lost every pending approval, and a
second worker process could never resume a thread it didn't start
(docs/deployment.md section 16). `PostgresSaver` (package
`langgraph-checkpoint-postgres==2.0.25`, the release line compatible with
the pinned `langgraph-checkpoint==2.1.2`) stores checkpoints in the SAME
PostgreSQL database this project already runs — no new infrastructure.
Restart recovery and cross-process resume are covered by
tests/agent_tasks/test_checkpointing.py against a real database.

WHY A CUSTOM SERIALIZER: checkpoints now persist agent state — including
untrusted repository content and LLM output — outside the process.
LangGraph's `JsonPlusSerializer` falls back to a JSON encoding when msgpack
rejects a string (invalid UTF-8, e.g. a lone surrogate), and its JSON
loader *revives* any `{"lc": 2, "type": "constructor", ...}` object by
importing and calling an arbitrary module attribute. Agent state only has
fixed-key dicts, so repository content cannot place such an object today,
but persisted data should not depend on that. `SafeCheckpointSerializer`
is msgpack-only: it refuses to write or read the JSON and pickle encodings,
so a value that cannot be encoded safely fails the task instead of taking
the fallback path.
"""

from typing import Any, Tuple

import psycopg
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

_SAFE_TYPES = ("msgpack", "null", "bytes", "bytearray")
_CONNECT_TIMEOUT_SECONDS = 5


class UnsafeCheckpointDataError(Exception):
    """A value would need (or was stored with) a non-msgpack encoding."""


class SafeCheckpointSerializer(JsonPlusSerializer):
    def __init__(self) -> None:
        super().__init__(pickle_fallback=False)

    def dumps_typed(self, obj: Any) -> Tuple[str, bytes]:
        try:
            type_, data = super().dumps_typed(obj)
        except TypeError as exc:
            # F8: which error a value msgpack cannot encode produces depends
            # on the ormsgpack version. 1.11 lets JsonPlusSerializer fall back
            # to JSON (refused below); 1.13 — what the 3.11 image resolves —
            # raises TypeError("string contains surrogates") straight through.
            # Both must surface as the same stable refusal, or the executor
            # reports `internal_error` instead of
            # `checkpoint_serialization_refused`. Never a fallback either way.
            raise UnsafeCheckpointDataError("refusing to checkpoint data msgpack cannot encode") from exc
        if type_ not in _SAFE_TYPES:
            raise UnsafeCheckpointDataError(f"refusing to checkpoint data with encoding {type_!r}")
        return type_, data

    def loads_typed(self, data: Tuple[str, bytes]) -> Any:
        if data[0] not in _SAFE_TYPES:
            raise UnsafeCheckpointDataError(f"refusing to load checkpoint data with encoding {data[0]!r}")
        return super().loads_typed(data)


def memory_checkpointer() -> MemorySaver:
    """In-process checkpointer (tests, evaluation, demo scripts)."""
    return MemorySaver(serde=SafeCheckpointSerializer())


def connect_checkpoint_database(database_url: str) -> psycopg.Connection:
    # The settings PostgresSaver requires: autocommit (each checkpoint write
    # is durable as soon as `put` returns), dict rows, and no server-side
    # prepared statements (safe behind connection poolers).
    return psycopg.connect(
        database_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,  # type: ignore[arg-type]
        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
    )


def postgres_checkpointer(conn: psycopg.Connection) -> PostgresSaver:
    return PostgresSaver(conn, serde=SafeCheckpointSerializer())  # type: ignore[arg-type]


def setup_postgres_checkpointer(database_url: str) -> None:
    """Creates/migrates LangGraph's checkpoint tables. Idempotent (the
    library tracks applied migrations in `checkpoint_migrations`)."""
    with connect_checkpoint_database(database_url) as conn:
        postgres_checkpointer(conn).setup()
