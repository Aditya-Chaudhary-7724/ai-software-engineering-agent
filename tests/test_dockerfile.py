"""Guards the production backend image's process command
(`backend/Dockerfile`'s CMD).

Regression for: the CMD hardcoded `--workers 2`. Gunicorn reads
WEB_CONCURRENCY only as the DEFAULT for its `workers` setting, and an
explicit command-line flag overrides that default, so WEB_CONCURRENCY was
silently ignored (a container started with WEB_CONCURRENCY=1 still ran 2
workers).

The worker count is resolved with Gunicorn's OWN config and argument
parser, in a fresh subprocess per case: Gunicorn computes the
WEB_CONCURRENCY default when `gunicorn.config` is imported, so the
environment must be set before that import, never patched afterwards.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

DOCKERFILE = Path(__file__).resolve().parent.parent / "backend" / "Dockerfile"

# Mirrors gunicorn.app.base.Application.load_config(): parse the command
# line, then apply every option that was actually given on top of the
# defaults (which already include WEB_CONCURRENCY).
_RESOLVE_WORKERS = """
import json, sys
from gunicorn.config import Config
cfg = Config()
args = cfg.parser().parse_args(json.loads(sys.argv[1]))
for key, value in vars(args).items():
    if value is not None and key != "args" and key.lower() in cfg.settings:
        cfg.set(key.lower(), value)
print(cfg.workers)
"""


def _dockerfile_cmd() -> List[str]:
    text = DOCKERFILE.read_text()
    match = re.search(r"^CMD\s+(\[.*?\])\s*$", text.replace("\\\n", ""), re.MULTILINE | re.DOTALL)
    assert match, "backend/Dockerfile must use the exec (JSON array) form of CMD"
    return json.loads(match.group(1))


def _resolved_workers(cmd: List[str], web_concurrency: Optional[str]) -> int:
    env = {key: value for key, value in os.environ.items() if key != "WEB_CONCURRENCY"}
    if web_concurrency is not None:
        env["WEB_CONCURRENCY"] = web_concurrency
    assert cmd[0] == "gunicorn"
    result = subprocess.run(
        [sys.executable, "-c", _RESOLVE_WORKERS, json.dumps(cmd[1:])],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return int(result.stdout.strip())


def test_the_cmd_does_not_hardcode_a_worker_count():
    cmd = _dockerfile_cmd()
    assert "--workers" not in cmd and "-w" not in cmd
    assert not any(arg.startswith("--workers=") for arg in cmd)


def test_web_concurrency_controls_the_worker_count():
    assert _resolved_workers(_dockerfile_cmd(), "3") == 3


def test_one_worker_when_web_concurrency_is_unset():
    assert _resolved_workers(_dockerfile_cmd(), None) == 1


def test_an_explicit_workers_flag_would_override_web_concurrency():
    # Documents WHY the flag must stay out of the CMD: this was the bug.
    cmd_with_flag = _dockerfile_cmd() + ["--workers", "2"]
    assert _resolved_workers(cmd_with_flag, "1") == 2
