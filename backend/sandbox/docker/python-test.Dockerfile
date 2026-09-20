# Minimal sandbox image for the Phase 10 Docker test runner.
#
# Only pytest is preinstalled. This deliberately does NOT try to be a
# general-purpose Python environment: a target repository's own extra
# dependencies (e.g. numpy, requests) are not installed here, because
# installing them would require network access inside the sandbox,
# which is disabled by default for security (see docs/architecture.md,
# "Sandbox"). Running a target repository's test suite when it needs
# additional third-party dependencies is an explicit, documented
# limitation of this phase, not something faked.
#
# Build locally (never pushed to any registry):
#   docker build -t ai-swe-agent-sandbox-python:latest \
#       -f backend/sandbox/docker/python-test.Dockerfile backend/sandbox/docker

FROM python:3.11-slim

RUN pip install --no-cache-dir --disable-pip-version-check pytest==8.3.4

WORKDIR /workspace
