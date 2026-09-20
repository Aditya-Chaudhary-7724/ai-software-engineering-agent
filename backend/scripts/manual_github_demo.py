"""Manual, human-readable demonstration of Phase 11's GitHub
integration.

Demonstrates, against a REAL public repository (`octocat/Hello-World` —
GitHub's own canonical example repo) over a real network connection:
1. Repository URL validation (including rejecting bad/malicious URLs).
2. Real repository metadata retrieval.
3. A real `git clone` into a controlled, path-traversal-safe workspace,
   wired into the EXISTING Phase 1/2/3 pipeline unchanged (ingestion +
   indexing) via `github_integration.pipeline`.
4. Local branch creation and a local commit (never pushed).
5. Proving push and pull-request creation are refused without explicit
   authorization/approval — WITHOUT actually pushing anything, since
   this script does not own `octocat/Hello-World` and must never write
   to a repository it doesn't control. Real push/PR creation requires a
   real `GITHUB_TOKEN` and a repository the caller actually owns —
   the same "mechanism proven, destructive action needs real
   credentials and explicit authorization" pattern as every other
   phase's demo.

Requires network access to api.github.com and github.com, and reachable
PostgreSQL (with pgvector) for the pipeline step. No GITHUB_TOKEN is
required or used — everything demonstrated here is read-only against
GitHub plus local-only git operations.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/manual_github_demo.py
"""

import getpass
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.store import VectorStore

from github_integration.exceptions import InvalidRepositoryURLError, UnauthorizedActionError
from github_integration.pipeline import ingest_github_repository
from github_integration.service import GitHubIntegrationService
from github_integration.url_validation import parse_github_url

DATABASE_URL = os.environ.get("DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent")
PUBLIC_TEST_REPO_URL = "https://github.com/octocat/Hello-World"


def main() -> None:
    print("--- 1. URL validation ---")
    for url in [
        PUBLIC_TEST_REPO_URL,
        "https://github.com/octocat/Hello-World.git",
        "git@github.com:octocat/Hello-World.git",  # rejected: SSH form out of scope
        "https://github.com/octocat/../../../etc",  # rejected: path traversal attempt
        "https://gitlab.com/octocat/Hello-World",  # rejected: wrong host
    ]:
        try:
            ref = parse_github_url(url)
            print(f"  OK:      {url!r} -> {ref.owner}/{ref.repo}")
        except InvalidRepositoryURLError as exc:
            print(f"  REJECTED: {url!r} ({exc})")
    print()

    service = GitHubIntegrationService(token=None)  # read-only; no GITHUB_TOKEN needed for this demo

    print("--- 2. Real repository metadata ---")
    metadata = service.get_repository_metadata(PUBLIC_TEST_REPO_URL)
    print(f"  {metadata.full_name}: default_branch={metadata.default_branch!r}, private={metadata.private}")
    print()

    with tempfile.TemporaryDirectory(prefix="github-demo-workspace-") as workspace_root:
        print("--- 3. Real clone into a controlled workspace, wired into the existing Phase 1/2/3 pipeline ---")
        vector_store = VectorStore(DATABASE_URL)
        embedding_provider = DeterministicLocalEmbeddingProvider()
        try:
            # ingest_github_repository clones (Phase 11) and then runs the
            # EXISTING, unchanged Phase 1/2/3 pipeline against the clone in
            # one call — see github_integration/pipeline.py.
            result = ingest_github_repository(
                PUBLIC_TEST_REPO_URL, workspace_root, vector_store, embedding_provider
            )
            cloned = result.cloned
            print(f"  Cloned to: {cloned.local_path}")
            print(
                f"  (workspace root: {workspace_root} — clone destination is always root/owner/repo, never elsewhere)"
            )
            print(
                f"  Ingested {result.ingestion_result.repository.relevant_files} file(s); "
                f"indexed {result.index_result.chunks_indexed} chunk(s) (repository_id={result.index_result.repository_id})."
            )

            print()
            print("--- 4. Local branch + local commit (never pushed) ---")
            service.create_branch(cloned, "demo/local-only-change")
            (Path(cloned.local_path) / "DEMO_NOTE.txt").write_text("Created by manual_github_demo.py\n")
            sha = service.commit_all(
                cloned, "Demo: local-only change", author_name="Demo Script", author_email="demo@example.com"
            )
            print(f"  Committed locally on branch 'demo/local-only-change': {sha[:12]}")
            print("  (This commit exists only in the temporary local clone — nothing was pushed.)")
        finally:
            conn = vector_store.connect()
            with conn.cursor() as cur:
                cur.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE;")
            conn.commit()
            conn.close()

        print()
        print("--- 5. Push and pull-request creation are refused without explicit authorization ---")
        try:
            service.push_branch(cloned, "demo/local-only-change", authorized=False)
            print("  ERROR: should have refused — this should never print")
        except UnauthorizedActionError as exc:
            print(f"  Correctly refused push: {exc}")

        try:
            service.create_pull_request(cloned, "demo/local-only-change", "Demo PR", "body", approved=False)
            print("  ERROR: should have refused — this should never print")
        except UnauthorizedActionError as exc:
            print(f"  Correctly refused pull request: {exc}")

        print()
        print(
            "  A real push and pull request require a real GITHUB_TOKEN, authorized=True / approved=True, "
            "and a repository the caller actually owns — never demonstrated here against "
            "octocat/Hello-World, which this script does not control."
        )


if __name__ == "__main__":
    main()
