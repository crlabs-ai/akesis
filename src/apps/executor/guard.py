"""Credential guard for the validation executor. Standard library only.

`python -m src.apps.executor` runs this before importing any Akesis module, because the shared
`Settings` loads `.env` from the working directory and the environment at import time. The executor
must never hold GitHub, Slack, Gemini, database, read-API or sign-in credentials.
"""

import os
from collections.abc import Mapping
from pathlib import Path

# Matched case-insensitively, as pydantic-settings matches environment names.
FORBIDDEN_ENVIRONMENT = frozenset(
    {
        "DATABASE_URL",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
        "GEMINI_API_KEY",
        "SLACK_WEBHOOK_URL",
        "SLACK_SIGNING_SECRET",
        "READ_API_TOKEN",
        "GITHUB_APP_CLIENT_SECRET",
        "AUTH_TOKEN_ENCRYPTION_KEY",
        "SANDBOX_EXECUTOR_REQUEST_KEY",
        "SANDBOX_EXECUTOR_RESPONSE_KEY",
    }
)


class UnsafeExecutorEnvironment(RuntimeError):
    """The executor refuses to start. The message names what was found, never its value."""


def refuse_unsafe_environment(cwd: Path, environ: Mapping[str, str]) -> None:
    if os.path.lexists(cwd / ".env"):
        raise UnsafeExecutorEnvironment(
            "A .env file exists in the executor working directory; refusing to start"
        )
    if environ.get("ENVIRONMENT", environ.get("environment", "")).lower() in ("production", "prod"):
        # The executor takes no application environment; production Settings would also demand
        # every application credential at import.
        raise UnsafeExecutorEnvironment("ENVIRONMENT must not be set for the executor")
    found = sorted(name for name in environ if name.upper() in FORBIDDEN_ENVIRONMENT)
    if found:
        raise UnsafeExecutorEnvironment(
            "Application credentials are set in the executor environment: " + ", ".join(found)
        )
