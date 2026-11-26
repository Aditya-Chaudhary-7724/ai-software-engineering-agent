"""Static and scripted checks of the production deployment (deploy/).

These encode the security properties of docker-compose.prod.yml and the
behaviour of the shell helpers, so a later edit cannot silently publish a
database port, hand the API the Docker socket, or leak a secret to the
wrong container. The live end-to-end rehearsal is recorded in
docs/deployment-progress.md; tests needing Docker skip without it.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
COMPOSE = yaml.safe_load((DEPLOY / "docker-compose.prod.yml").read_text())
SERVICES = COMPOSE["services"]
SCRIPTS = ["lib.sh", "deploy.sh", "backup.sh", "restore.sh", "duckdns.sh", "bootstrap.sh", "make-prod-env.sh"]


def _docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")


def _env(service: str) -> dict:
    return SERVICES[service].get("environment", {})


# -- docker-compose.prod.yml --------------------------------------------------


def test_only_caddy_publishes_ports():
    published = {name for name, svc in SERVICES.items() if svc.get("ports")}
    assert published == {"caddy"}
    assert sorted(SERVICES["caddy"]["ports"]) == ["443:443", "443:443/udp", "80:80"]


def test_databases_are_on_an_internal_network_only():
    assert COMPOSE["networks"]["data"].get("internal") is True
    assert SERVICES["postgres"]["networks"] == ["data"]
    assert SERVICES["neo4j"]["networks"] == ["data"]
    # Caddy and the frontend can never reach a database.
    assert "data" not in SERVICES["caddy"]["networks"]
    assert "data" not in SERVICES["frontend"]["networks"]


def test_the_api_never_gets_the_docker_socket_and_never_runs_the_sandbox():
    for name, svc in SERVICES.items():
        for volume in svc.get("volumes", []):
            assert "docker.sock" not in volume, f"{name} mounts the Docker socket"
        assert not svc.get("privileged"), name
    assert _env("backend")["SANDBOX_ENABLED"] == "false"


def test_each_container_gets_only_its_own_secrets():
    frontend, backend = set(_env("frontend")), set(_env("backend"))
    for secret in ("GROQ_API_KEY", "DATABASE_URL", "NEO4J_PASSWORD", "POSTGRES_PASSWORD"):
        assert secret not in frontend
    for secret in ("SESSION_SECRET", "OPERATOR_PASSWORD_HASH"):
        assert secret not in backend
    for name in ("caddy", "postgres", "neo4j"):
        env = set(_env(name))
        assert not env & {"API_SERVICE_TOKEN", "DEMO_API_SERVICE_TOKEN", "GROQ_API_KEY", "SESSION_SECRET"}, name
    # Host-only values never reach any container.
    everything = " ".join(str(v) for svc in SERVICES.values() for v in svc.get("environment", {}).values())
    for host_only in ("DUCKDNS_TOKEN", "OFFSITE_BACKUP_PAR_URL"):
        assert host_only not in everything


def test_production_settings_are_pinned():
    assert _env("backend")["APP_ENV"] == "production"
    assert _env("backend")["LLM_PROVIDER"] == "groq"
    assert _env("backend")["EMBEDDING_PROVIDER"] == "fastembed"
    assert _env("frontend")["TRUST_PROXY_HEADERS"] == "true"  # Caddy overwrites X-Forwarded-For
    assert _env("frontend")["APP_ORIGIN"] == "https://${SITE_ADDRESS}"
    assert _env("frontend")["BACKEND_URL"] == "http://backend:8000"


def test_every_container_has_a_memory_limit_restart_policy_and_log_rotation():
    for name, svc in SERVICES.items():
        assert svc.get("mem_limit"), name
        assert svc.get("restart") == "unless-stopped", name
        assert svc["logging"]["options"]["max-size"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name


def test_app_containers_drop_capabilities_and_run_read_only():
    for name in ("caddy", "frontend", "backend"):
        assert SERVICES[name]["cap_drop"] == ["ALL"], name
        assert SERVICES[name]["read_only"] is True, name
    assert SERVICES["caddy"]["cap_add"] == ["NET_BIND_SERVICE"]


def test_images_are_pinned_and_app_images_are_tagged_by_the_deploy():
    for name in ("caddy", "postgres", "neo4j"):
        image = SERVICES[name]["image"]
        assert ":" in image and not image.endswith(":latest"), image
    for name in ("frontend", "backend"):
        assert SERVICES[name]["image"].endswith(":${IMAGE_TAG:?IMAGE_TAG is set by deploy.sh}") or ":${IMAGE_TAG:?}" in SERVICES[name]["image"]


@needs_docker
def test_compose_file_is_valid_and_refuses_missing_required_values(tmp_path):
    env = tmp_path / "prod.env"
    base = [f"{k}=x" for k in ("SITE_ADDRESS", "IMAGE_REGISTRY", "POSTGRES_PASSWORD", "NEO4J_PASSWORD",
                                "API_SERVICE_TOKEN", "SESSION_SECRET", "OPERATOR_PASSWORD_HASH", "GROQ_API_KEY",
                                "LLM_MODEL")]
    env.write_text("\n".join(base) + "\n")
    cmd = ["docker", "compose", "-f", str(DEPLOY / "docker-compose.prod.yml"), "--env-file", str(env), "config", "--quiet"]
    ok = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "IMAGE_TAG": "abc1234"}, timeout=60)
    assert ok.returncode == 0, ok.stderr
    env.write_text("\n".join(b for b in base if not b.startswith("GROQ_API_KEY")) + "\n")
    missing = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "IMAGE_TAG": "abc1234"}, timeout=60)
    assert missing.returncode != 0 and "GROQ_API_KEY" in missing.stderr


# -- Caddyfile ------------------------------------------------------------------


def test_caddy_routes_only_health_to_the_backend_and_hides_api_docs():
    text = (DEPLOY / "Caddyfile").read_text()
    assert text.count("reverse_proxy backend:8000") == 1
    assert "path /health/live /health/ready" in text
    assert "@apidocs path /docs /docs/* /redoc /redoc/* /openapi.json" in text
    assert "header_up X-Forwarded-For {remote_host}" in text
    assert "admin off" in text


@needs_docker
def test_caddyfile_is_valid():
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "SITE_ADDRESS=demo.example.duckdns.org",
         "-v", f"{DEPLOY / 'Caddyfile'}:/etc/caddy/Caddyfile:ro", SERVICES["caddy"]["image"],
         "caddy", "validate", "--config", "/etc/caddy/Caddyfile"],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr[-1500:]


# -- shell scripts --------------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_are_strict_and_executable(script):
    path = DEPLOY / script
    text = path.read_text()
    assert "set -euo pipefail" in text or 'source "$(dirname "$0")/lib.sh"' in text, script
    if script != "lib.sh":
        assert path.stat().st_mode & stat.S_IXUSR, f"{script} is not executable"


def test_no_script_prints_or_sources_the_env_file():
    for script in SCRIPTS:
        text = (DEPLOY / script).read_text()
        assert "source \"$ENV_FILE\"" not in text and ". \"$ENV_FILE\"" not in text, script
        assert "cat \"$ENV_FILE\"" not in text and "set -x" not in text, script


@needs_docker
def test_shellcheck_reports_no_warnings():
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{DEPLOY}:/mnt:ro", "koalaman/shellcheck:v0.10.0", "-S", "warning", "-x", "-P", "/mnt",
         *[f"/mnt/{s}" for s in SCRIPTS if s != "lib.sh"]],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout[-3000:]


def _bash(snippet: str, env_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY / "lib.sh"}"; {snippet}'],
        capture_output=True, text=True, env={**os.environ, "ENV_FILE": str(env_file)}, timeout=30,
    )


def test_env_get_reads_one_key_without_executing_the_file(tmp_path):
    env = tmp_path / "prod.env"
    marker = tmp_path / "pwned"
    env.write_text(f"A=1\nEVIL=$(touch {marker})\nB='quoted'\nA=2\n")
    assert _bash("env_get A", env).stdout == "2"  # last wins, like compose
    assert _bash("env_get B", env).stdout == "quoted"
    assert _bash("env_get MISSING", env).stdout == ""
    _bash("env_get EVIL >/dev/null", env)
    assert not marker.exists()


def test_require_env_file_refuses_a_readable_file(tmp_path):
    env = tmp_path / "prod.env"
    env.write_text("A=1\n")
    env.chmod(0o644)
    result = _bash("require_env_file", env)
    assert result.returncode != 0 and "chmod 600" in result.stderr
    env.chmod(0o600)
    assert _bash("require_env_file", env).returncode == 0


def test_deploy_refuses_a_tag_that_is_not_a_git_sha(tmp_path):
    result = subprocess.run([str(DEPLOY / "deploy.sh"), "latest; rm -rf /"], capture_output=True, text=True,
                            env={**os.environ, "ENV_FILE": str(tmp_path / "none")}, timeout=30)
    assert result.returncode != 0 and "not a git SHA" in result.stderr


def test_make_prod_env_generates_secrets_once_never_prints_them_and_enables_demo_later(tmp_path):
    target = tmp_path / "cfg" / "prod.env"
    run = lambda *args: subprocess.run([str(DEPLOY / "make-prod-env.sh"), *args], capture_output=True, text=True,
                                       env={**os.environ, "PROD_ENV": str(target)}, timeout=30)
    first = run()
    assert first.returncode == 0
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    values = dict(line.split("=", 1) for line in target.read_text().splitlines() if "=" in line and not line.startswith("#"))
    for key in ("POSTGRES_PASSWORD", "NEO4J_PASSWORD", "API_SERVICE_TOKEN", "SESSION_SECRET"):
        assert len(values[key]) >= 32 and "$" not in values[key]
        assert values[key] not in first.stdout + first.stderr
    assert values["DEMO_API_SERVICE_TOKEN"] == ""  # demo off until seeded
    assert "Still missing" in first.stdout and "GROQ_API_KEY" in first.stdout
    run()
    again = dict(line.split("=", 1) for line in target.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert again["API_SERVICE_TOKEN"] == values["API_SERVICE_TOKEN"]  # never rotated by a re-run
    assert run("--enable-demo", "1;rm").returncode == 2
    enabled = run("--enable-demo", "1,2,3")
    assert enabled.returncode == 0
    final = dict(line.split("=", 1) for line in target.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert final["DEMO_REPOSITORY_IDS"] == "1,2,3"
    assert len(final["DEMO_API_SERVICE_TOKEN"]) >= 32 and final["DEMO_API_SERVICE_TOKEN"] != final["API_SERVICE_TOKEN"]
    assert final["DEMO_API_SERVICE_TOKEN"] not in enabled.stdout


def test_the_template_holds_no_values():
    for line in (DEPLOY / "prod.env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            assert value == "" or key == "IMAGE_REGISTRY", f"{key} has a value in the template"
