"""Runs the real worker entry point as its own OS process, for multi-process and E2E tests.

GitHub is stubbed at the HTTP transport inside this process by tests.support.github_stub. Every
request is appended to a shared JSON-lines log tagged with the process ID, before any delay.

Environment:
  AKESIS_TEST_GITHUB_LOG          shared log path (required)
  AKESIS_TEST_GITHUB_DELAY        seconds to hold each installation request (default 0)
  AKESIS_TEST_GITHUB_RETRY_AFTER  Retry-After for unscripted installations (default 3600)
"""

import os

from tests.support.github_stub import install_stub, make_handler

_LOG = os.environ["AKESIS_TEST_GITHUB_LOG"]
_DELAY = float(os.environ.get("AKESIS_TEST_GITHUB_DELAY", "0"))
_RETRY_AFTER = os.environ.get("AKESIS_TEST_GITHUB_RETRY_AFTER", "3600")

install_stub(make_handler(log_path=_LOG, default_retry_after=_RETRY_AFTER, delay=_DELAY))

if __name__ == "__main__":
    from src.apps.worker.__main__ import main

    main()
