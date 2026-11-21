"""Entry point: ``python -m src.apps.worker``.

Runs the durable retry worker until SIGTERM or SIGINT. The worker finishes the deliveries it has
already claimed, then exits without claiming more. It shares PostgreSQL with the API and keeps no
state in memory that a restart would lose.
"""

import asyncio
import signal

from src.packages.database.session import get_session_factory
from src.packages.shared.config import settings
from src.packages.shared.logging import configure_logging, get_logger
from src.packages.shared.webhook_worker import (
    HeartbeatFile,
    LogRetrievalConfig,
    RetryWorker,
    WorkerConfig,
)
from src.packages.shared.workflow_logs import LogLimits

logger = get_logger("akesis.worker")


def build_config() -> WorkerConfig:
    return WorkerConfig(
        poll_seconds=settings.worker_poll_seconds,
        batch_size=settings.worker_batch_size,
        max_attempts=settings.webhook_retry_max_attempts,
        base_delay_seconds=settings.webhook_retry_base_delay_seconds,
        max_delay_seconds=settings.webhook_retry_max_delay_seconds,
        provider_ceiling_seconds=settings.webhook_retry_provider_delay_ceiling_seconds,
        db_backoff_max_seconds=settings.worker_db_backoff_max_seconds,
        verification_deadline_seconds=settings.webhook_verification_deadline_seconds,
        github_api_url=settings.github_api_url,
        github_app_id=settings.github_app_id,
        github_app_private_key=settings.github_app_private_key,
        log_retrieval=LogRetrievalConfig(
            max_attempts=settings.log_retrieval_max_attempts,
            deadline_seconds=settings.log_retrieval_deadline_seconds,
            lease_seconds=settings.log_retrieval_lease_seconds,
            limits=LogLimits.from_settings(),
        ),
    )


async def run() -> None:
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    heartbeat = HeartbeatFile(settings.worker_heartbeat_path)
    worker = RetryWorker(get_session_factory(), build_config())
    logger.info("webhook_worker_started", poll_seconds=settings.worker_poll_seconds)
    await worker.run_forever(stop, heartbeat.write)
    logger.info("webhook_worker_stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
