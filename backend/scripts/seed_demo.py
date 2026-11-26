"""Operator-only: index the public-demo repositories and generate their
pre-generated examples. Idempotent: re-running skips what already exists.

Runs INSIDE the backend container, against the running API, so it uses the
real ingestion worker exactly as the operator UI does:

    docker compose -f docker-compose.prod.yml --env-file prod.env exec backend \\
        python scripts/seed_demo.py --confirm-real-calls

1. For each repository in demo/seed_repos.json: submit an ingestion job
   (with the knowledge graph when Neo4j is configured) through
   POST /api/v1/ingestion-jobs, authenticated with API_SERVICE_TOKEN, and wait
   for it. An already-ingested repository is reused (409
   repository_already_ingested carries its id); --reindex re-indexes it.
2. Prints the line to put in prod.env: DEMO_REPOSITORY_IDS=<ids>.
3. Unless --skip-examples: runs generate_demo_examples.py for each repository
   that has no examples yet (or all of them with --replace). That makes real
   LLM calls on the OPERATOR model, hence --confirm-real-calls.

Exit codes: 0 done, 2 refused (configuration), 3 an ingestion or a
generation failed (the others still ran; nothing half-written is stored).
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "demo" / "seed_repos.json"
TERMINAL = {"completed", "failed"}


class SeedError(Exception):
    pass


class ApiClient:
    """The few /api/v1 calls seeding needs. Never logs the token."""

    def __init__(self, base_url: str, token: str, session: Optional[requests.Session] = None) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> requests.Response:
        return self._session.request(method, self._base + path, json=body, timeout=30, allow_redirects=False)

    def graph_available(self) -> bool:
        response = self._request("GET", "/capabilities")
        if response.status_code != 200:
            raise SeedError(f"capabilities: HTTP {response.status_code} {_code(response)}")
        graph = response.json().get("graph", {})
        return bool(graph.get("ok")) and graph.get("detail") == "reachable"

    def submit(self, github_url: str, build_graph: bool) -> Dict:
        response = self._request("POST", "/ingestion-jobs", {"github_url": github_url, "build_graph": build_graph})
        if response.status_code in (200, 202):
            return {"job": response.json()["job"]}
        if response.status_code == 409 and _code(response) == "repository_already_ingested":
            return {"existing_repository_id": int(response.json()["repository_id"])}
        raise SeedError(f"submit {github_url}: HTTP {response.status_code} {_code(response)}")

    def reindex(self, repository_id: int, build_graph: bool) -> Dict:
        response = self._request("POST", f"/repositories/{repository_id}/reindex", {"build_graph": build_graph})
        if response.status_code in (200, 202):
            return response.json()["job"]
        raise SeedError(f"reindex {repository_id}: HTTP {response.status_code} {_code(response)}")

    def job(self, job_id: str) -> Dict:
        response = self._request("GET", f"/ingestion-jobs/{job_id}")
        if response.status_code != 200:
            raise SeedError(f"job {job_id}: HTTP {response.status_code} {_code(response)}")
        return response.json()


def _code(response: requests.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return ""


def wait_for(client: ApiClient, job: Dict, timeout_seconds: float, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> Dict:
    deadline = clock() + timeout_seconds
    while job["status"] not in TERMINAL:
        if clock() > deadline:
            raise SeedError(f"job {job['id']} did not finish within {int(timeout_seconds)} s (stage {job['stage']})")
        sleep(5)
        job = client.job(job["id"])
    return job


def load_config(path: Path) -> List[Dict]:
    data = json.loads(path.read_text())
    repos = data.get("repositories") if isinstance(data, dict) else None
    if not isinstance(repos, list) or not repos:
        raise SeedError("the config must be {\"repositories\": [...]}")
    for repo in repos:
        if not isinstance(repo.get("github_url"), str) or not isinstance(repo.get("questions"), list):
            raise SeedError("every repository needs github_url and questions")
    return repos


def index_all(client: ApiClient, repos: List[Dict], reindex: bool, timeout_seconds: float,
              log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Optional[int]]:
    build_graph = client.graph_available()
    log(f"knowledge graph: {'on' if build_graph else 'off (Neo4j not reachable)'}")
    ids: Dict[str, Optional[int]] = {}
    for repo in repos:
        url = repo["github_url"]
        try:
            submitted = client.submit(url, build_graph)
            if "existing_repository_id" in submitted:
                repository_id = submitted["existing_repository_id"]
                if not reindex:
                    log(f"{url}: already ingested as repository {repository_id} (use --reindex to re-index)")
                    ids[url] = repository_id
                    continue
                job = client.reindex(repository_id, build_graph)
            else:
                job = submitted["job"]
            log(f"{url}: job {job['id']} {job['status']}")
            job = wait_for(client, job, timeout_seconds, sleep=sleep)
            if job["status"] != "completed":
                raise SeedError(f"{url}: ingestion failed ({job.get('error_code')})")
            ids[url] = job["repository_id"]
            log(f"{url}: repository {job['repository_id']}, graph {job['graph_status']}")
        except SeedError as exc:
            log(f"FAILED: {exc}")
            ids[url] = None
    return ids


def generate_all(repos: List[Dict], ids: Dict[str, Optional[int]], replace: bool, confirm: bool,
                 generate: Callable[[List[str]], int], log: Callable[[str], None] = print) -> bool:
    ok = True
    for repo in repos:
        repository_id = ids.get(repo["github_url"])
        if repository_id is None:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"questions": repo["questions"], "task": repo.get("task")}, handle)
            request_path = handle.name
        argv = ["--repository-id", str(repository_id), "--input", request_path]
        argv += ["--replace"] if replace else []
        argv += ["--confirm-real-calls"] if confirm else []
        try:
            code = generate(argv)
        finally:
            os.unlink(request_path)
        if code == 2 and not replace:
            log(f"repository {repository_id}: examples not generated (see the reason above; --replace regenerates)")
        elif code != 0:
            ok = False
    return ok


def _load_generator() -> Callable[[List[str]], int]:
    import importlib.util

    script = Path(__file__).resolve().parent / "generate_demo_examples.py"
    spec = importlib.util.spec_from_file_location("generate_demo_examples", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def main(argv: Optional[List[str]] = None, client: Optional[ApiClient] = None,
         generate: Optional[Callable[[List[str]], int]] = None, sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(description="Index the demo repositories and generate their examples.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--reindex", action="store_true", help="re-index repositories that already exist")
    parser.add_argument("--skip-examples", action="store_true")
    parser.add_argument("--replace", action="store_true", help="regenerate examples that already exist")
    parser.add_argument("--confirm-real-calls", action="store_true", help="required for examples: real LLM calls")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    args = parser.parse_args(argv)

    try:
        repos = load_config(Path(args.config))
    except (OSError, ValueError, SeedError) as exc:
        print(f"Refusing: invalid config: {exc}")
        return 2
    if client is None:
        token = os.environ.get("API_SERVICE_TOKEN", "")
        if len(token) < 32:
            print("Refusing: API_SERVICE_TOKEN is not set (the operator token, >= 32 characters).")
            return 2
        client = ApiClient(args.api_url, token)
    try:
        ids = index_all(client, repos, args.reindex, args.timeout_seconds, sleep=sleep)
    except (SeedError, requests.RequestException) as exc:
        print(f"Refusing: the API is not usable: {type(exc).__name__}: {exc}")
        return 2
    seeded = [str(i) for i in ids.values() if i is not None]
    print(f"DEMO_REPOSITORY_IDS={','.join(seeded)}")
    failed = len(seeded) < len(repos)
    if not args.skip_examples:
        if generate is None:
            generate = _load_generator()
        if not generate_all(repos, ids, args.replace, args.confirm_real_calls, generate):
            failed = True
    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
