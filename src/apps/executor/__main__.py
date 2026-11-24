"""Entry point: ``python -m src.apps.executor``.

Order matters: the stdlib credential guard runs before any module that loads `Settings` (and
therefore `.env`) is imported. Then the shared settings are checked again, executor-labelled
containers and stale workspaces are removed, and the Unix socket is bound with SO_PEERCRED checks.
"""

import os
import sys
from pathlib import Path

from src.apps.executor.guard import UnsafeExecutorEnvironment, refuse_unsafe_environment


def main() -> int:
    try:
        refuse_unsafe_environment(Path.cwd(), os.environ)
    except UnsafeExecutorEnvironment as error:
        print(f"akesis-executor: {error}", file=sys.stderr)
        return 2

    import asyncio  # noqa: PLC0415 - deliberately after the guard

    import uvicorn  # noqa: PLC0415

    from src.apps.executor.app import (  # noqa: PLC0415
        ExecutorConfig,
        ExecutorStartupError,
        bind_socket,
        create_app,
        peer_checked_protocol,
        require_clean_settings,
        startup_cleanup,
    )

    try:
        require_clean_settings()
        config = ExecutorConfig()  # type: ignore[call-arg]  # values come from the environment
        startup_cleanup(Path(config.work_dir))
        sock = bind_socket(Path(config.socket_path))
    except (ExecutorStartupError, ValueError) as error:
        print(f"akesis-executor: {error}", file=sys.stderr)
        return 2
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config),
            http=peer_checked_protocol(config.uids()),
            lifespan="off",
            log_level="warning",
            access_log=False,
        )
    )
    asyncio.run(server.serve(sockets=[sock]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
