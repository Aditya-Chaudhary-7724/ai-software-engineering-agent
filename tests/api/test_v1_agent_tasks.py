"""F6 agent-task + approval endpoints, through the real FastAPI app and the
real test PostgreSQL.

The worker is a recording stub (`app.state.agent_worker`) for request-level
tests; the end-to-end test drives the REAL executor between HTTP calls.
"""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from agent.approval import diff_sha256
from agent_tasks.store import AgentTaskStore
from api.v1 import API_V1_PREFIX
from jobs.worker import BackgroundWorker

from tests.agent_tasks.conftest import MODIFY_INSTRUCTION, ORIGINAL_CONTENT, clean_agent_tables, make_executor  # noqa: F401
from tests.api.conftest import TEST_DATABASE_URL, auth_headers, insert_repository, requires_postgres

pytestmark = requires_postgres

TASKS = f"{API_V1_PREFIX}/agent-tasks"
DIFF = "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n-def greet():\n+def greet():  # changed\n"
DIGEST = diff_sha256(DIFF)
INTERRUPT = "0123456789abcdef" * 2


class RecordingWorker:
    is_running = True

    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(ORIGINAL_CONTENT)
    return root


@pytest.fixture
def task_api(app, api_client, monkeypatch, vector_store, clean_agent_tables, repo):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    worker = RecordingWorker()
    app.state.agent_worker = worker
    repository_id = insert_repository(vector_store, str(repo.resolve()), name="repo")
    return api_client, worker, repository_id


def _awaiting(repository_id, root, *, log=None, response=None):
    store = AgentTaskStore(TEST_DATABASE_URL)
    task = store.create(repository_id=repository_id, target_path=str(root.resolve()), instruction="fix it", route="modify")
    store.claim_next("worker-test")
    return store.await_approval(
        task.id, interrupt_id=INTERRUPT, diff=DIFF, diff_sha256=DIGEST, relative_path="main.py",
        message="This change requires human approval before it is applied.", fix_iteration=0,
        approval_ttl_seconds=3600, execution_log=log or ["Proposed a change to 'main.py'; awaiting human approval."],
        outcome={"generator": "stub"},
    )


def _decide(client, task_id, **body):
    payload = {"approved": True, "interrupt_id": INTERRUPT, "diff_sha256": DIGEST, **body}
    return client.post(f"{TASKS}/{task_id}/decision", json=payload, headers=auth_headers())


def _status(task_id):
    return AgentTaskStore(TEST_DATABASE_URL).get(task_id).status


# -- route preview --------------------------------------------------------------


@pytest.mark.parametrize(
    "instruction, route, supported, approval",
    [
        ("Where is greet defined?", "answer", True, False),
        ("Fix the greet function", "modify", True, True),
        ("Run the tests", "test", False, False),
    ],
)
def test_route_preview_uses_the_agents_classifier(task_api, instruction, route, supported, approval):
    client, _, _ = task_api
    body = client.post(f"{TASKS}/route-preview", json={"instruction": instruction}, headers=auth_headers()).json()
    assert body == {"route": route, "supported": supported, "requires_approval": approval}


# -- submission -----------------------------------------------------------------


def test_submit_enqueues_and_never_returns_the_server_path(task_api, repo):
    client, worker, repository_id = task_api
    response = client.post(
        f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": MODIFY_INSTRUCTION}, headers=auth_headers()
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued" and body["route"] == "modify" and body["proposal"] is None
    assert body["trace_id"] == body["id"]
    assert str(repo.resolve()) not in response.text and "target_path" not in response.text
    assert worker.notifications == 1


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"instruction": "fix", "root_path": "/etc"}, 422, "invalid_request"),
        ({"instruction": "fix", "approved": True}, 422, "invalid_request"),
        ({"instruction": "   "}, 422, "invalid_instruction"),
        ({"instruction": "x" * 2001}, 422, "invalid_request"),
        ({"instruction": "Run the tests"}, 422, "route_not_supported"),
    ],
)
def test_submit_rejects_bad_bodies(task_api, body, status, code):
    client, worker, repository_id = task_api
    response = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json=body, headers=auth_headers())
    assert response.status_code == status and response.json()["error"] == code
    assert worker.notifications == 0


def test_only_one_active_task_per_repository(task_api):
    client, _, repository_id = task_api
    url = f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks"
    assert client.post(url, json={"instruction": "Fix greet"}, headers=auth_headers()).status_code == 202
    second = client.post(url, json={"instruction": "Fix greet again"}, headers=auth_headers())
    assert second.status_code == 409 and second.json() == {"error": "agent_task_active"}


def test_submit_and_decide_require_a_running_agent_worker(app, task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    app.state.agent_worker = None
    submit = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": "Fix"}, headers=auth_headers())
    assert submit.status_code == 503 and submit.json() == {"error": "agent_worker_unavailable"}
    assert _decide(client, task.id).status_code == 503
    assert _status(task.id) == "awaiting_approval"  # nothing was consumed


# -- the approval gate over HTTP -------------------------------------------------


@pytest.mark.parametrize("approved", ["no", "yes", "false", "true", 1, 0, {}, [], None, 1.0], ids=repr)
def test_only_a_json_boolean_is_accepted(task_api, repo, approved):
    client, worker, repository_id = task_api
    task = _awaiting(repository_id, repo)

    response = _decide(client, task.id, approved=approved)

    assert response.status_code == 422 and response.json()["fields"] == ["body.approved"]
    assert _status(task.id) == "awaiting_approval"
    assert worker.notifications == 0


@pytest.mark.parametrize(
    "body",
    [
        {"diff_sha256": None},
        {"diff_sha256": "abc"},
        {"diff_sha256": DIGEST.upper()},
        {"interrupt_id": None},
        {"interrupt_id": "../../etc"},
        {"extra": "field"},
    ],
    ids=repr,
)
def test_malformed_bindings_are_rejected(task_api, repo, body):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    assert _decide(client, task.id, **body).status_code == 422
    assert _status(task.id) == "awaiting_approval"


def test_a_missing_binding_is_rejected(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    response = client.post(f"{TASKS}/{task.id}/decision", json={"approved": True}, headers=auth_headers())
    assert response.status_code == 422
    assert set(response.json()["fields"]) == {"body.interrupt_id", "body.diff_sha256"}


def test_a_changed_diff_hash_is_refused(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    response = _decide(client, task.id, diff_sha256=diff_sha256(DIFF + "+evil\n"))
    assert response.status_code == 409 and response.json() == {"error": "approval_diff_mismatch"}
    assert _status(task.id) == "awaiting_approval"


def test_a_stale_interrupt_is_refused(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    response = _decide(client, task.id, interrupt_id="f" * 32)
    assert response.status_code == 409 and response.json() == {"error": "approval_stale"}


def test_an_approval_cannot_be_reused(task_api, repo):
    client, worker, repository_id = task_api
    task = _awaiting(repository_id, repo)
    first = _decide(client, task.id)
    assert first.status_code == 202 and first.json()["status"] == "queued"
    assert first.json()["approvals_granted"] == 1 and first.json()["proposal"]["state"] == "approved"

    again = _decide(client, task.id)
    reject_after = _decide(client, task.id, approved=False)
    assert again.status_code == 409 and again.json() == {"error": "approval_not_pending"}
    assert reject_after.status_code == 409
    assert worker.notifications == 1


def test_an_expired_approval_is_refused(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_tasks SET approval_expires_at = now() - interval '1 second' WHERE id = %s", (task.id,))
    conn.commit()
    conn.close()

    response = _decide(client, task.id)
    assert response.status_code == 409 and response.json() == {"error": "approval_expired"}
    shown = client.get(f"{TASKS}/{task.id}", headers=auth_headers()).json()
    assert shown["proposal"]["state"] == "expired" and shown["proposal"]["interrupt_id"] is None


def test_concurrent_http_decisions_exactly_one_is_accepted(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda i: _decide(client, task.id, approved=i % 2 == 0).status_code, range(8)))

    assert statuses.count(202) == 1
    assert statuses.count(409) == 7


@pytest.mark.parametrize("task_id, status", [("not-a-uuid", 400), ("00000000-0000-0000-0000-000000000000", 404)])
def test_unknown_or_invalid_task_ids(task_api, task_id, status):
    client, _, _ = task_api
    assert _decide(client, task_id).status_code == status
    assert client.get(f"{TASKS}/{task_id}", headers=auth_headers()).status_code == status


# -- safe output ----------------------------------------------------------------


def test_agent_output_is_scrubbed_but_the_diff_is_exact(task_api, repo):
    client, _, repository_id = task_api
    root = str(repo.resolve())
    task = _awaiting(
        repository_id,
        repo,
        log=[f"Could not read '{root}/main.py': [Errno 2] No such file: '/private/var/tmp/x'", "token ghp_" + "a" * 30],
    )

    body = client.get(f"{TASKS}/{task.id}", headers=auth_headers())
    text = body.text
    assert root not in text and "/private/var" not in text and "ghp_" + "a" * 30 not in text
    shown = body.json()
    assert shown["execution_log"][0].startswith("Could not read 'main.py'")
    assert shown["proposal"]["diff"] == DIFF and shown["proposal"]["diff_sha256"] == DIGEST
    assert shown["proposal"]["state"] == "pending" and shown["proposal"]["interrupt_id"] == INTERRUPT
    assert shown["outcome"]["is_placeholder"] is True


def test_list_tasks(task_api, repo):
    client, _, repository_id = task_api
    task = _awaiting(repository_id, repo)
    listed = client.get(TASKS, params={"repository_id": repository_id}, headers=auth_headers()).json()["tasks"]
    assert [t["id"] for t in listed] == [task.id]


# -- end to end with the real executor --------------------------------------------


def test_full_task_flow_over_http_with_the_real_agent(task_api, vector_store, repo):  # noqa: F811
    """submit -> worker proposes -> GET shows the diff -> approve with the
    shown binding -> worker applies. Real graph, real PostgresSaver."""
    from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
    from vectorstore.service import IndexingService

    client, _, _ = task_api
    repository_id = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store).index_repository(str(repo)).repository_id
    store, executor = make_executor()
    worker = BackgroundWorker(store, executor, name="agent-test", recover=executor.recover_stale)

    submitted = client.post(
        f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": MODIFY_INSTRUCTION}, headers=auth_headers()
    ).json()
    assert worker.run_once()

    shown = client.get(f"{TASKS}/{submitted['id']}", headers=auth_headers()).json()
    assert shown["status"] == "awaiting_approval"
    proposal = shown["proposal"]
    assert proposal["relative_path"] == "main.py" and diff_sha256(proposal["diff"]) == proposal["diff_sha256"]
    assert (repo / "main.py").read_text() == ORIGINAL_CONTENT  # proposed, NOT applied

    decided = _decide(client, submitted["id"], interrupt_id=proposal["interrupt_id"], diff_sha256=proposal["diff_sha256"])
    assert decided.status_code == 202
    assert worker.run_once()

    final = client.get(f"{TASKS}/{submitted['id']}", headers=auth_headers()).json()
    assert final["status"] == "completed", final["error_code"]
    assert final["outcome"]["applied"] is True and final["outcome"]["changes_applied"] == 1
    assert final["proposal"]["state"] == "approved"
    assert (repo / "main.py").read_text() != ORIGINAL_CONTENT


# -- SANDBOX_ENABLED off (the hosted-deployment default) ---------------------------


def test_a_disabled_sandbox_applies_the_change_but_reports_tests_not_run(task_api, vector_store, repo):  # noqa: F811
    """Full HTTP flow with the real graph and PostgresSaver, using the runner
    the worker gets when SANDBOX_ENABLED is off: the approved change is
    written, and the outcome says tests did NOT run, never that they passed."""
    from sandbox.disabled_runner import DisabledTestRunner
    from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
    from vectorstore.service import IndexingService

    client, _, _ = task_api
    (repo / "test_main.py").write_text("def test_placeholder():\n    assert True\n")  # tests exist, but must not run
    repository_id = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store).index_repository(str(repo)).repository_id
    store, executor = make_executor(test_runner=DisabledTestRunner())
    worker = BackgroundWorker(store, executor, name="agent-test", recover=executor.recover_stale)

    submitted = client.post(
        f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks", json={"instruction": MODIFY_INSTRUCTION}, headers=auth_headers()
    ).json()
    assert worker.run_once()
    proposal = client.get(f"{TASKS}/{submitted['id']}", headers=auth_headers()).json()["proposal"]
    assert _decide(client, submitted["id"], interrupt_id=proposal["interrupt_id"], diff_sha256=proposal["diff_sha256"]).status_code == 202
    assert worker.run_once()

    final = client.get(f"{TASKS}/{submitted['id']}", headers=auth_headers()).json()
    assert final["status"] == "completed", final["error_code"]
    assert final["outcome"]["applied"] is True
    assert final["outcome"]["test_ran"] is False
    assert final["outcome"]["test_passed"] is None
    assert final["outcome"]["test_skipped_reason"] == "sandbox_disabled"
    assert "Tests not run: sandbox disabled in this deployment." in final["final_response"]
    assert "not verified" in final["final_response"]
    assert "Tests: passed" not in final["final_response"]
    assert any("Sandbox disabled" in entry for entry in final["execution_log"])
