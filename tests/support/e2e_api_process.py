"""Runs the production API under uvicorn with GitHub stubbed at the transport. End-to-end use only.

Start from a directory with no .env. Configuration comes from the environment, and the database
must be the disposable akesis_test database.
"""

import os

import uvicorn

from src.apps.api.main import create_app
from src.packages.shared.config import Settings
from tests.support.github_stub import install_stub, make_handler


def main() -> None:
    install_stub(make_handler(log_path=os.environ.get("AKESIS_TEST_GITHUB_LOG")))
    app = create_app(Settings())
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("AKESIS_E2E_API_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
