"""Test isolation for database-backed tests.

Tests must never reach the developer's `.env` database. Settings read DATABASE_URL from the
environment before any test module imports them, so this module chooses the value first:

- AKESIS_DISPOSABLE_TEST_DATABASE_URL set: must be a loopback `akesis_test` database. Anything
  else aborts the test session. It becomes DATABASE_URL.
- Not set: DATABASE_URL points at a closed loopback port. Database-only tests are skipped, and
  any other test that tries to reach a database fails fast instead of writing anywhere.

See docs/05-development/local-development.md ("Database-backed tests") for the setup command.
"""

import os
from urllib.parse import urlsplit

import pytest

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_DISABLED_DATABASE_URL = "postgresql+asyncpg://akesis_tests:disabled@127.0.0.1:9/akesis_disabled"
_DISPOSABLE_ENV = "AKESIS_DISPOSABLE_TEST_DATABASE_URL"

# Individual tests in otherwise database-free files that read the database.
_DATABASE_TEST_NODES = frozenset(
    {
        "tests/unit/test_read_api_security.py::test_read_endpoints_accessible_in_dev_without_token",
        "tests/unit/test_read_api_security.py::test_valid_read_token_permits_access",
    }
)

# Files whose tests cannot run without the disposable database.
_DATABASE_TEST_FILES = frozenset(
    {
        "tests/benchmark/test_vertical_slice_benchmarks.py",
        "tests/integration/test_approval_flow.py",
        "tests/integration/test_database_repository.py",
        "tests/integration/test_historical_recovery.py",
        "tests/integration/test_provenance_matrix.py",
        "tests/integration/test_snapshot_boundary.py",
        "tests/integration/test_incident_routes.py",
        "tests/integration/test_mutation_flow.py",
        "tests/integration/test_orchestrator_flow.py",
        "tests/integration/test_webhook_api.py",
        "tests/integration/test_workflow_log_worker.py",
    }
)

_disposable_url = os.environ.get(_DISPOSABLE_ENV, "")
if _disposable_url:
    _parsed = urlsplit(_disposable_url)
    if _parsed.hostname not in _LOOPBACK_HOSTS or _parsed.path != "/akesis_test":
        raise RuntimeError(
            f"{_DISPOSABLE_ENV} must be a loopback akesis_test database; refusing to run tests."
        )
    os.environ["DATABASE_URL"] = _disposable_url
else:
    os.environ["DATABASE_URL"] = _DISABLED_DATABASE_URL


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip database-backed tests when no disposable test database is configured."""
    if _disposable_url:
        return
    skip = pytest.mark.skip(reason=f"requires {_DISPOSABLE_ENV} (disposable akesis_test database)")
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        if path in _DATABASE_TEST_FILES or item.nodeid in _DATABASE_TEST_NODES:
            item.add_marker(skip)
