"""F7 regression tests for agent tasks over HTTP: concurrent submissions,
cross-task replay of an approval binding, decisions on interrupted tasks,
and repository boundaries. Real FastAPI app + real test PostgreSQL;
F6's own tests (test_v1_agent_tasks.py) are not repeated here."""

import shutil
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from agent.approval import diff_sha256
from agent_tasks.store import AgentTaskStore
from api.v1 import API_V1_PREFIX

from tests.agent_tasks.conftest import ORIGINAL_CONTENT, clean_agent_tables  # noqa: F401
from tests.api.conftest import TEST_DATABASE_URL, auth_headers, insert_repository, requires_postgres

pytestmark = requires_postgres

TASKS = f"{API_V1_PREFIX}/agent-tasks"


class RecordingWorker:
    is_running = True

    def notify(self):
        pass


@pytest.fixture
def api(app, api_client, monkeypatch, vector_store, clean_agent_tables, tmp_path):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    app.state.agent_worker = RecordingWorker()

    def make_repo(name):
        root = tmp_path / name
        root.mkdir()
        (root / "main.py").write_text(ORIGINAL_CONTENT)
        return root, insert_repository(vector_store, str(root.resolve()), name=name)

    return api_client, make_repo


def _awaiting(repository_id, root, interrupt_id, diff):
    store = AgentTaskStore(TEST_DATABASE_URL)
    task = store.create(repository_id=repository_id, target_path=str(root.resolve()), instruction="fix it", route="modify")
    assert store.claim_next("worker-test").id == task.id
    return store.await_approval(
        task.id, interrupt_id=interrupt_id, diff=diff, diff_sha256=diff_sha256(diff), relative_path="main.py",
        message="approve?", fix_iteration=0, approval_ttl_seconds=3600, execution_log=[], outcome={"generator": "stub"},
    )


def _decide(client, task_id, interrupt_id, digest, approved=True):
    return client.post(
        f"{TASKS}/{task_id}/decision",
        json={"approved": approved, "interrupt_id": interrupt_id, "diff_sha256": digest},
        headers=auth_headers(),
    )


def test_concurrent_http_submissions_create_exactly_one_task(api):
    client, make_repo = api
    _, repository_id = make_repo("repo")
    url = f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks"

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda i: client.post(url, json={"instruction": f"Fix greet {i}"}, headers=auth_headers()), range(8)))

    codes = sorted(r.status_code for r in responses)
    assert codes.count(202) == 1 and codes.count(409) == 7
    assert all(r.json() == {"error": "agent_task_active"} for r in responses if r.status_code == 409)
    assert len(AgentTaskStore(TEST_DATABASE_URL).list_recent(50, repository_id)) == 1


def test_one_repositorys_active_task_does_not_block_another(api):
    client, make_repo = api
    _, first = make_repo("first")
    _, second = make_repo("second")
    for repository_id in (first, second):
        response = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": "Fix greet"}, headers=auth_headers())
        assert response.status_code == 202


def test_an_approval_binding_cannot_be_replayed_against_another_task(api):
    """Task B's (interrupt id, diff hash) — both valid for B — must not
    decide task A, even when A's diff is byte-identical to B's."""
    client, make_repo = api
    root_a, repo_a = make_repo("a")
    root_b, repo_b = make_repo("b")
    diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-x\n+y\n"
    task_a = _awaiting(repo_a, root_a, "a" * 32, diff)
    task_b = _awaiting(repo_b, root_b, "b" * 32, diff)

    replay = _decide(client, task_a.id, task_b.proposal_interrupt_id, task_b.proposal_diff_sha256)

    assert replay.status_code == 409 and replay.json() == {"error": "approval_stale"}
    assert AgentTaskStore(TEST_DATABASE_URL).get(task_a.id).status == "awaiting_approval"
    assert _decide(client, task_b.id, task_b.proposal_interrupt_id, task_b.proposal_diff_sha256).status_code == 202


def test_a_decision_on_an_interrupted_task_is_refused_and_it_stays_interrupted(api):
    client, make_repo = api
    root, repository_id = make_repo("repo")
    diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-x\n+y\n"
    task = _awaiting(repository_id, root, "c" * 32, diff)
    store = AgentTaskStore(TEST_DATABASE_URL)
    assert _decide(client, task.id, "c" * 32, diff_sha256(diff)).status_code == 202
    store.claim_next("worker-that-died")
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task.id,))
    conn.commit()
    conn.close()
    store.recover_stale(120)

    again = _decide(client, task.id, "c" * 32, diff_sha256(diff))
    shown = client.get(f"{TASKS}/{task.id}", headers=auth_headers()).json()

    assert again.status_code == 409 and again.json() == {"error": "approval_not_pending"}
    assert shown["status"] == "interrupted" and shown["error_code"] == "worker_interrupted"
    assert shown["outcome"]["changes_applied"] == 0
    assert shown["decided_at"] is not None  # the UI can tell the operator an approval had been given


def test_submitting_for_a_repository_whose_files_are_gone_is_refused(api):
    client, make_repo = api
    root, repository_id = make_repo("gone")
    shutil.rmtree(root)
    response = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": "Fix greet"}, headers=auth_headers())
    assert response.status_code == 409 and response.json() == {"error": "repository_files_unavailable"}


@pytest.mark.parametrize("repository_id, status", [("999999999", 404), ("0", 422), ("-1", 422), ("abc", 422)])
def test_submitting_for_an_unknown_or_malformed_repository_is_refused(api, repository_id, status):
    client, _ = api
    response = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": "Fix"}, headers=auth_headers())
    assert response.status_code == status


def test_listing_by_repository_never_returns_another_repositorys_tasks(api):
    client, make_repo = api
    root_a, repo_a = make_repo("a")
    root_b, repo_b = make_repo("b")
    _awaiting(repo_a, root_a, "d" * 32, "x")
    _awaiting(repo_b, root_b, "e" * 32, "y")
    listed = client.get(TASKS, params={"repository_id": repo_a}, headers=auth_headers()).json()["tasks"]
    assert {t["repository_id"] for t in listed} == {repo_a}
