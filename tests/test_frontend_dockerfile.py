"""Guards frontend/Dockerfile (the production Next.js image)."""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "frontend" / "Dockerfile"
TEXT = DOCKERFILE.read_text()


def test_node_24_on_both_stages():
    bases = re.findall(r"^FROM\s+(\S+)", TEXT, re.MULTILINE)
    assert len(bases) == 2
    for base in bases:
        assert re.fullmatch(r"node:24\.\d+\.\d+-bookworm-slim( AS build)?", base) or base.startswith("node:24."), base


def test_runs_unprivileged_on_all_interfaces_inside_the_container():
    final_stage = TEXT.split("\nFROM ")[-1]
    assert re.search(r"^USER node$", final_stage, re.MULTILINE)
    assert "HOSTNAME=0.0.0.0" in final_stage
    assert 'CMD ["node", "server.js"]' in final_stage


def test_no_secret_is_passed_at_build_time():
    for name in ("SESSION_SECRET", "API_SERVICE_TOKEN", "DEMO_API_SERVICE_TOKEN", "OPERATOR_PASSWORD_HASH"):
        assert not re.search(rf"^(ARG|ENV)\s+{name}", TEXT, re.MULTILINE), name


def test_only_the_standalone_output_is_copied_into_the_final_image():
    final_stage = TEXT.split("\nFROM ")[-1]
    copies = re.findall(r"^COPY .*$", final_stage, re.MULTILINE)
    assert copies == [
        "COPY --from=build --chown=node:node /app/.next/standalone ./",
        "COPY --from=build --chown=node:node /app/.next/static ./.next/static",
    ]
