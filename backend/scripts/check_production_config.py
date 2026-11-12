"""Phase 15: safe production configuration validation.

Verifies required/optional environment variables are SET — never
prints their VALUES. Reads the exact same `backend/config.py` list the
API's own readiness check consults, so this script and the running
application can never disagree about what's required.

Intended use: run this before deploying (locally, or as a CI/deploy
pipeline step) to catch a missing required setting BEFORE the
application is started with a broken configuration, rather than
discovering it from a failed request in production.

Exit code: 0 if every REQUIRED setting is configured, 1 otherwise.
Optional settings missing are reported but never fail the check — an
optional capability being unavailable is an honest, expected state
(see backend/config.py's own docstring), not a configuration error.

Run from the repository root:

    set -a; source .env; set +a
    .venv/bin/python backend/scripts/check_production_config.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import check_environment, feature_flags, llm_configuration_problems, llm_provider_name, missing_required_settings


def main() -> None:
    statuses = check_environment()
    flags = feature_flags()
    name_width = max(len(name) for name in [status.name for status in statuses] + [flag.name for flag in flags])

    print("Configuration check (values are never printed):")
    print()
    for status in statuses:
        if status.configured:
            label = "configured"
        elif status.required:
            label = "MISSING (required)"
        else:
            label = "not set (optional)"
        print(f"  {status.name:<{name_width}}  {label}")

    # Feature flags are not secrets: their effective state is printed.
    print()
    print("Feature flags (effective state):")
    print()
    for flag in flags:
        print(f"  {flag.name:<{name_width}}  {'enabled' if flag.enabled else 'disabled'}")

    # The provider NAME is not a secret; key values are never printed.
    llm_problems = llm_configuration_problems()
    print()
    print(f"LLM provider: {llm_provider_name()}")
    for problem in llm_problems:
        print(f"  PROBLEM: {problem}")

    missing = missing_required_settings()
    print()
    if missing:
        print(f"FAILED: {len(missing)} required setting(s) missing: {', '.join(missing)}")
        sys.exit(1)
    if llm_problems:
        print(f"FAILED: {len(llm_problems)} LLM configuration problem(s).")
        sys.exit(1)

    print("OK: all required settings are configured.")
    sys.exit(0)


if __name__ == "__main__":
    main()
