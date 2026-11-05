"""F6: PostgreSQL checkpointing — compatibility, restart recovery, and the
fail-closed serializer.

Pinned versions under test: langgraph 0.6.11, langgraph-checkpoint 2.1.2,
langgraph-checkpoint-postgres 2.0.25 (requirements.txt).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.checkpointing import SafeCheckpointSerializer, UnsafeCheckpointDataError, setup_postgres_checkpointer

from tests.agent_tasks.conftest import (
    ORIGINAL_CONTENT,
    TEST_DATABASE_URL,
    checkpoint_exists,
    make_service,
    new_thread_id,
    requires_postgres,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _helper(*args) -> dict:
    env = {**os.environ, "PYTHONPATH": f"{REPO_ROOT / 'backend'}{os.pathsep}{REPO_ROOT}", "TEST_DATABASE_URL": TEST_DATABASE_URL}
    completed = subprocess.run(
        [sys.executable, "-m", "tests.agent_tasks.restart_helper", *map(str, args)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


@requires_postgres
def test_setup_is_idempotent(clean_agent_tables):
    setup_postgres_checkpointer(TEST_DATABASE_URL)
    setup_postgres_checkpointer(TEST_DATABASE_URL)


@requires_postgres
def test_a_pending_approval_survives_a_process_restart(indexed_repo):
    """Process A pauses for approval and EXITS. Process B — a fresh
    interpreter — sees the same pending interrupt (same id) from PostgreSQL
    and resumes it; the approved change is applied."""
    thread_id = new_thread_id()
    paused = _helper("pause", indexed_repo["repository_id"], indexed_repo["root_path"], thread_id)
    assert paused["status"] == "awaiting_approval"
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT

    resumed = _helper("resume", thread_id, paused["interrupt_id"], paused["diff_sha256"])

    assert resumed["pending_seen"] == {"interrupt_id": paused["interrupt_id"], "diff_sha256": paused["diff_sha256"]}
    assert resumed["status"] == "completed" and resumed["applied"] is True
    assert (indexed_repo["root"] / "main.py").read_text() != ORIGINAL_CONTENT


@requires_postgres
def test_a_thread_paused_in_one_service_resumes_in_another_with_its_own_connection(vector_store, indexed_repo):
    """Two independent services/connections — the shape of two API worker
    processes sharing one database."""
    from agent.checkpointing import connect_checkpoint_database

    conn_a, conn_b = connect_checkpoint_database(TEST_DATABASE_URL), connect_checkpoint_database(TEST_DATABASE_URL)
    try:
        thread_id = new_thread_id()
        paused = make_service(vector_store, conn_a).run(
            "Fix the greet function", indexed_repo["repository_id"], indexed_repo["root_path"], thread_id
        )
        conn_a.close()

        result = make_service(vector_store, conn_b).resume(thread_id, approved=False, **paused.approval_binding())
        assert result.status == "completed" and result.approved is False
        assert checkpoint_exists(thread_id)
    finally:
        conn_b.close()


def test_serializer_round_trips_plain_agent_state():
    serde = SafeCheckpointSerializer()
    state = {"question": "fix it", "execution_log": ["a", "b"], "approved": None, "ranked_candidates": [{"x": 1.5}]}
    assert serde.loads_typed(serde.dumps_typed(state)) == state


def test_serializer_refuses_to_take_the_json_fallback_on_write():
    """A lone surrogate makes msgpack fail; stock JsonPlusSerializer would
    then store JSON. The safe serializer refuses instead."""
    with pytest.raises(UnsafeCheckpointDataError):
        SafeCheckpointSerializer().dumps_typed({"final_response": "bad \ud800 text"})


@pytest.mark.parametrize(
    "stored",
    [
        ("json", b'{"lc": 2, "type": "constructor", "id": ["os", "system"], "args": ["echo pwned"]}'),
        ("pickle", b"\x80\x04K\x01."),
    ],
)
def test_serializer_refuses_to_load_json_or_pickle_checkpoint_data(stored, monkeypatch):
    called = []
    monkeypatch.setattr(os, "system", lambda *a: called.append(a))
    with pytest.raises(UnsafeCheckpointDataError):
        SafeCheckpointSerializer().loads_typed(stored)
    assert called == []
