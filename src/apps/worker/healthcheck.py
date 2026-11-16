"""Readiness probe: ``python -m src.apps.worker.healthcheck``.

Exits 0 when the worker's heartbeat is fresh and in the ``ok`` state. Exits 1 otherwise, with a
one-line reason. It reads only the heartbeat file, so it works without database access.
"""

import sys

from src.packages.shared.config import settings
from src.packages.shared.webhook_worker import check_heartbeat


def main() -> int:
    healthy, reason = check_heartbeat(
        settings.worker_heartbeat_path,
        max_age_seconds=settings.worker_heartbeat_max_age_seconds,
    )
    print(f"akesis-worker: {reason}")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
