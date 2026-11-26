"""backend/scripts/seed_demo.py: indexing the demo repositories through the
API and generating their examples, with fakes (no network, no LLM)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "seed_demo.py"
CONFIG = Path(__file__).resolve().parents[2] / "backend" / "demo" / "seed_repos.json"


def _load():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = _load()


class FakeClient:
    def __init__(self, existing=None, fail=None, graph=True):
        self.existing = existing or {}
        self.fail = fail or set()
        self.graph = graph
        self.submitted, self.reindexed = [], []
        self._jobs, self._next_id = {}, 10

    def graph_available(self):
        return self.graph

    def _new_job(self, url, repository_id):
        job_id = f"job-{len(self._jobs)}"
        status = "failed" if url in self.fail else "completed"
        self._jobs[job_id] = {"id": job_id, "status": status, "stage": "done", "repository_id": repository_id,
                              "graph_status": "completed" if self.graph else "not_requested", "error_code": "x" if url in self.fail else None}
        return {"id": job_id, "status": "queued", "stage": "queued"}

    def submit(self, url, build_graph):
        self.submitted.append((url, build_graph))
        if url in self.existing:
            return {"existing_repository_id": self.existing[url]}
        self._next_id += 1
        return {"job": self._new_job(url, self._next_id)}

    def reindex(self, repository_id, build_graph):
        self.reindexed.append(repository_id)
        url = next(u for u, i in self.existing.items() if i == repository_id)
        return self._new_job(url, repository_id)

    def job(self, job_id):
        return self._jobs[job_id]


def _config(tmp_path, n=2):
    repos = [{"github_url": f"https://github.com/o/r{i}", "questions": ["q?"], "task": "t"} for i in range(n)]
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"repositories": repos}))
    return path


def test_the_shipped_config_names_three_small_demo_repositories():
    repos = seed.load_config(CONFIG)
    assert [r["github_url"] for r in repos] == [
        "https://github.com/pallets/markupsafe",
        "https://github.com/pallets/itsdangerous",
        "https://github.com/psf/requests",
    ]
    for repo in repos:
        assert 1 <= len(repo["questions"]) <= 5  # generate_demo_examples' bound
        assert isinstance(repo["task"], str) and repo["task"]


def test_indexes_new_repositories_and_prints_the_demo_ids(tmp_path, capsys):
    client, calls = FakeClient(), []
    code = seed.main(["--config", str(_config(tmp_path)), "--confirm-real-calls"], client=client,
                     generate=lambda argv: calls.append(argv) or 0, sleep=lambda s: None)
    assert code == 0
    assert client.submitted == [("https://github.com/o/r0", True), ("https://github.com/o/r1", True)]
    assert "DEMO_REPOSITORY_IDS=11,12" in capsys.readouterr().out
    assert [a[:2] for a in calls] == [["--repository-id", "11"], ["--repository-id", "12"]]
    assert all("--confirm-real-calls" in a and "--replace" not in a for a in calls)


def test_reuses_an_already_ingested_repository_without_reindexing(tmp_path, capsys):
    client = FakeClient(existing={"https://github.com/o/r0": 4})
    code = seed.main(["--config", str(_config(tmp_path)), "--skip-examples"], client=client, sleep=lambda s: None)
    assert code == 0
    assert client.reindexed == []
    assert "DEMO_REPOSITORY_IDS=4,11" in capsys.readouterr().out


def test_reindex_flag_reindexes_an_existing_repository(tmp_path):
    client = FakeClient(existing={"https://github.com/o/r0": 4})
    seed.main(["--config", str(_config(tmp_path)), "--skip-examples", "--reindex"], client=client, sleep=lambda s: None)
    assert client.reindexed == [4]


def test_without_neo4j_the_graph_is_not_requested(tmp_path):
    client = FakeClient(graph=False)
    seed.main(["--config", str(_config(tmp_path, 1)), "--skip-examples"], client=client, sleep=lambda s: None)
    assert client.submitted == [("https://github.com/o/r0", False)]


def test_a_failed_ingestion_is_reported_and_skipped_but_the_others_continue(tmp_path, capsys):
    client, calls = FakeClient(fail={"https://github.com/o/r0"}), []
    code = seed.main(["--config", str(_config(tmp_path)), "--confirm-real-calls"], client=client,
                     generate=lambda argv: calls.append(argv) or 0, sleep=lambda s: None)
    assert code == 3
    out = capsys.readouterr().out
    assert "FAILED" in out and "DEMO_REPOSITORY_IDS=12" in out
    assert [a[1] for a in calls] == ["12"]


def test_a_failed_generation_fails_the_run(tmp_path):
    code = seed.main(["--config", str(_config(tmp_path, 1)), "--confirm-real-calls"], client=FakeClient(),
                     generate=lambda argv: 3, sleep=lambda s: None)
    assert code == 3


def test_existing_examples_are_not_an_error_without_replace(tmp_path):
    # generate_demo_examples refuses (2) to overwrite without --replace.
    code = seed.main(["--config", str(_config(tmp_path, 1))], client=FakeClient(), generate=lambda argv: 2, sleep=lambda s: None)
    assert code == 0


def test_the_generator_gets_a_request_file_with_the_questions_and_task(tmp_path):
    seen = {}

    def generate(argv):
        seen.update(json.loads(Path(argv[argv.index("--input") + 1]).read_text()))
        return 0

    seed.main(["--config", str(_config(tmp_path, 1)), "--replace", "--confirm-real-calls"], client=FakeClient(),
              generate=generate, sleep=lambda s: None)
    assert seen == {"questions": ["q?"], "task": "t"}


def test_refuses_without_the_operator_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)
    assert seed.main(["--config", str(_config(tmp_path))]) == 2
    assert "API_SERVICE_TOKEN" in capsys.readouterr().out


def test_a_job_that_never_finishes_times_out():
    class Stuck(FakeClient):
        def job(self, job_id):
            return {"id": job_id, "status": "running", "stage": "indexing"}

    ticks = iter(range(0, 10_000, 100))
    with pytest.raises(seed.SeedError, match="did not finish"):
        seed.wait_for(Stuck(), {"id": "j", "status": "queued", "stage": "queued"}, 300, sleep=lambda s: None, clock=lambda: next(ticks))


def test_api_client_sends_the_token_and_never_follows_redirects():
    sent = []

    class Session:
        headers = {}

        def request(self, method, url, **kwargs):
            sent.append((method, url, kwargs))

            class R:
                status_code = 409

                def json(self):
                    return {"error": "repository_already_ingested", "repository_id": 7}

            return R()

    client = seed.ApiClient("http://backend:8000/", "t" * 40, session=Session())
    assert client.submit("https://github.com/o/r", True) == {"existing_repository_id": 7}
    method, url, kwargs = sent[0]
    assert (method, url) == ("POST", "http://backend:8000/api/v1/ingestion-jobs")
    assert kwargs["allow_redirects"] is False and kwargs["json"] == {"github_url": "https://github.com/o/r", "build_graph": True}
    assert Session.headers["Authorization"] == "Bearer " + "t" * 40
