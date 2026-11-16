"""Durable retry worker for webhook verification (ADR-0010).

The worker executes scheduled verification attempts that the webhook route only schedules. It
runs as a separate process and keeps all state in PostgreSQL, so a crash or restart loses no
work that was not already claimed.

Guarantees:
- Due deliveries are selected with FOR UPDATE SKIP LOCKED and then claimed with the same atomic
  transition the route uses, so concurrent workers never verify the same attempt twice.
- Attempt budgets, provider delays and stale-write guards are enforced by the repository and
  the shared admission path, not re-implemented here.
- Verification input is rebuilt only from identity fields persisted after HMAC verification of
  the original delivery. No raw payload, signature, header, token or key is stored or read.
- Downstream admission is the same code the route runs. It never invokes remediation, so the
  temporary remediation hold applies to worker-driven deliveries too.
- A crash after claiming leaves the row ``verifying``. After the stale window it is reclaimed,
  and that reclaim counts as an attempt because verification may already have started.
- Database outages back off exponentially and keep the process alive. Shutdown finishes the
  deliveries already claimed and then stops without claiming more.
"""

import asyncio
import contextlib
import json
import os
import random
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.packages.database.models import WebhookEventModel
from src.packages.database.webhook_events import WebhookEventRepository
from src.packages.sdk.github_app_auth import GitHubAppAuth
from src.packages.shared.logging import get_logger
from src.packages.shared.models import WorkflowRunConclusion
from src.packages.shared.systemd_notify import notify
from src.packages.shared.webhook_admission import admit_verified_delivery
from src.packages.shared.webhook_retry import classify_retry
from src.packages.shared.workflow_provenance import (
    ProvenanceVerificationError,
    TransientProvenanceError,
    WorkflowRunProvenanceVerifier,
)

logger = get_logger(__name__)

STATE_OK = "ok"
STATE_DATABASE_UNAVAILABLE = "database_unavailable"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"
STATE_NOT_CONFIGURED = "github_app_not_configured"

_IDENTITY_FIELDS = (
    "installation_id",
    "event_repository_id",
    "event_repository_name",
    "run_id",
    "run_attempt",
    "source_repository_id",
    "source_repository_name",
    "source_sha",
    "source_branch",
)


@dataclass(frozen=True)
class WorkerConfig:
    poll_seconds: float
    batch_size: int
    max_attempts: int
    base_delay_seconds: int
    max_delay_seconds: int
    provider_ceiling_seconds: int
    db_backoff_max_seconds: float
    verification_deadline_seconds: float
    github_api_url: str
    github_app_id: int
    # Secret: never logged, never included in an error message or a heartbeat.
    github_app_private_key: str = field(repr=False)


@dataclass
class TickSummary:
    examined: int = 0
    claimed: int = 0
    verified: int = 0
    retried: int = 0
    held: int = 0
    rejected: int = 0
    exhausted: int = 0
    superseded: int = 0
    crashed: int = 0
    released: int = 0
    release_failed: int = 0
    app_not_configured: bool = False
    counters: dict[str, int] = field(default_factory=dict)


def build_verification_payload(row: WebhookEventModel) -> dict[str, Any] | None:
    """Rebuild the verifier's input from persisted, HMAC-authenticated identity fields.

    Returns None when any identity field is missing. Such a delivery cannot be verified and is
    rejected rather than guessed at.
    """
    if any(getattr(row, name) is None for name in _IDENTITY_FIELDS):
        return None
    full_name = str(row.event_repository_name)
    owner, _, name = full_name.partition("/")
    return {
        "action": row.action or "completed",
        "installation": {"id": row.installation_id},
        "repository": {
            "id": row.event_repository_id,
            "full_name": full_name,
            "name": name,
            "owner": {"login": owner},
        },
        "workflow_run": {
            "id": row.run_id,
            "run_attempt": row.run_attempt,
            "head_sha": row.source_sha,
            "head_branch": row.source_branch,
            "head_repository": {
                "id": row.source_repository_id,
                "full_name": row.source_repository_name,
            },
            "name": row.workflow_name or "Unnamed Workflow",
            "html_url": row.run_url or "",
            "conclusion": row.conclusion or "unknown",
        },
    }


def _conclusion(value: str | None) -> WorkflowRunConclusion:
    try:
        return WorkflowRunConclusion(value or "unknown")
    except ValueError:
        return WorkflowRunConclusion.UNKNOWN


class RetryWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: WorkerConfig,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._now = now
        self._random = random_value

    @property
    def app_configured(self) -> bool:
        return bool(self._config.github_app_id and self._config.github_app_private_key)

    async def tick(
        self,
        stop: asyncio.Event | None = None,
        progress: Callable[[], None] | None = None,
    ) -> TickSummary:
        """Run one pass: select due deliveries, claim or exhaust them, then process claims."""
        summary = TickSummary()
        if not self.app_configured:
            # Nothing is claimed, so no attempt is consumed and nothing is lost. Due rows stay
            # visible to operators until the credentials are configured.
            summary.app_not_configured = True
            return summary
        if stop is not None and stop.is_set():
            return summary

        max_attempts = self._config.max_attempts
        work: list[tuple[str, int]] = []
        async with self._sessions() as session:
            async with session.begin():
                repo = WebhookEventRepository(session)
                rows = await repo.select_due(self._config.batch_size)
                summary.examined = len(rows)
                for row in rows:
                    # Capture the attempt number before claiming: the claim's bulk UPDATE
                    # synchronises this ORM object, so reading it afterwards double-counts.
                    next_attempt = row.attempt_count + 1
                    if row.attempt_count >= max_attempts:
                        if await repo.exhaust(row.delivery_id, max_attempts):
                            summary.exhausted += 1
                    elif await repo.claim(row.delivery_id, max_attempts):
                        summary.claimed += 1
                        work.append((row.delivery_id, next_attempt))

        for index, (delivery_id, attempt) in enumerate(work):
            if stop is not None and stop.is_set():
                # Shutdown requested: never start another delivery. Release every claim that
                # has not started, so it returns to the schedule instead of waiting out the window.
                await self._release_unstarted(work[index:], summary)
                break
            await self._process(delivery_id, attempt, summary)
            if progress is not None:
                progress()
        return summary

    async def _release_unstarted(self, claims: list[tuple[str, int]], summary: TickSummary) -> None:
        for delivery_id, attempt in claims:
            try:
                async with self._sessions() as session:
                    async with session.begin():
                        released = await WebhookEventRepository(session).release_unstarted(
                            delivery_id, attempt
                        )
            except (OperationalError, InterfaceError, OSError) as error:
                # Database unavailable during shutdown: the claim is left to stale-claim recovery.
                summary.release_failed += 1
                logger.warning("webhook_worker_release_failed", error_type=type(error).__name__)
                continue
            if released:
                summary.released += 1
            else:
                summary.superseded += 1

    async def _process(self, delivery_id: str, attempt: int, summary: TickSummary) -> None:
        async with self._sessions() as session:
            row = await WebhookEventRepository(session).state(delivery_id)
        if row is None:
            summary.superseded += 1
            return
        payload = build_verification_payload(row)
        if payload is None:
            await self._record_failure(
                delivery_id,
                attempt,
                retryable=False,
                failure_code="identity_incomplete",
                headers={},
                summary=summary,
            )
            return

        try:
            # One deadline bounds every GitHub request in this verification. It is shorter than the
            # stale-claim window, so a live verification is never reclaimed while still running.
            async with asyncio.timeout(self._config.verification_deadline_seconds):
                async with httpx.AsyncClient(
                    base_url=self._config.github_api_url, timeout=30.0
                ) as api:
                    app_auth = GitHubAppAuth(
                        self._config.github_app_id,
                        self._config.github_app_private_key,
                        client=api,
                    )
                    provenance = await WorkflowRunProvenanceVerifier(app_auth, api).verify(
                        payload, delivery_id
                    )
        except TimeoutError:
            await self._record_failure(
                delivery_id,
                attempt,
                retryable=True,
                failure_code="verification_deadline_exceeded",
                headers={},
                summary=summary,
            )
            return
        except (ProvenanceVerificationError, ValueError) as error:
            retryable = isinstance(error, TransientProvenanceError)
            code = (
                error.code
                if isinstance(error, ProvenanceVerificationError)
                else "provenance_mismatch"
            )
            headers = error.headers if isinstance(error, TransientProvenanceError) else {}
            await self._record_failure(
                delivery_id,
                attempt,
                retryable=retryable,
                failure_code=code,
                headers=headers,
                summary=summary,
            )
            return
        except Exception as error:
            # Crash-equivalent: the row stays verifying and the stale-claim window recovers it.
            # Only the exception type is logged, because its message may contain sensitive data.
            summary.crashed += 1
            logger.error(
                "webhook_worker_verification_interrupted",
                delivery_id=delivery_id,
                attempt=attempt,
                error_type=type(error).__name__,
            )
            return

        async with self._sessions() as session:
            admission = await admit_verified_delivery(
                session,
                delivery_id=delivery_id,
                attempt_number=attempt,
                provenance=provenance,
                workflow_name=row.workflow_name or "Unnamed Workflow",
                run_url=row.run_url or "",
                conclusion=_conclusion(row.conclusion),
            )
        if admission.result == "verified":
            summary.verified += 1
        elif admission.result == "superseded":
            summary.superseded += 1
        else:
            # Duplicate or conflicting identity: a terminal outcome, recorded by admission.
            summary.counters[admission.result] = summary.counters.get(admission.result, 0) + 1

    async def _record_failure(
        self,
        delivery_id: str,
        attempt: int,
        *,
        retryable: bool,
        failure_code: str,
        headers: dict[str, str],
        summary: TickSummary,
    ) -> None:
        now = self._now()
        schedule = classify_retry(
            headers,
            attempt,
            now=now,
            base_delay=self._config.base_delay_seconds,
            max_delay=self._config.max_delay_seconds,
            provider_ceiling=self._config.provider_ceiling_seconds,
            random_value=self._random,
        )
        async with self._sessions() as session:
            async with session.begin():
                outcome = await WebhookEventRepository(session).record_attempt_failure(
                    delivery_id,
                    attempt,
                    retryable=retryable,
                    failure_code=failure_code,
                    next_retry_at=schedule.next_retry_at,
                    max_attempts=self._config.max_attempts,
                    retry_status=schedule.status,
                )
        if outcome == "retryable_error":
            summary.retried += 1
        elif outcome == "provider_hold":
            summary.held += 1
        elif outcome == "exhausted":
            summary.exhausted += 1
        elif outcome == "rejected":
            summary.rejected += 1
        else:
            summary.superseded += 1

    async def run_forever(
        self,
        stop: asyncio.Event,
        heartbeat: Callable[[str], None],
    ) -> None:
        """Poll until ``stop`` is set. Reports state through ``heartbeat`` after each pass."""
        consecutive_db_failures = 0

        def heartbeat_and_ping(state: str) -> None:
            heartbeat(state)
            notify("WATCHDOG=1")

        while not stop.is_set():
            if not self.app_configured:
                state = STATE_NOT_CONFIGURED
                delay = self._config.poll_seconds
            else:
                try:
                    await self.tick(stop, progress=lambda: heartbeat_and_ping(STATE_OK))
                    consecutive_db_failures = 0
                    state = STATE_OK
                    delay = self._config.poll_seconds
                except (OperationalError, InterfaceError, OSError) as error:
                    consecutive_db_failures += 1
                    state = STATE_DATABASE_UNAVAILABLE
                    delay = min(
                        self._config.db_backoff_max_seconds,
                        2.0 ** min(consecutive_db_failures, 10),
                    )
                    # Never log str(error): driver messages can echo the connection URL.
                    logger.warning(
                        "webhook_worker_database_unavailable",
                        consecutive_failures=consecutive_db_failures,
                        retry_in_seconds=delay,
                        error_type=type(error).__name__,
                    )
                except Exception as error:
                    state = STATE_ERROR
                    delay = self._config.poll_seconds
                    logger.error("webhook_worker_tick_failed", error_type=type(error).__name__)
            heartbeat_and_ping(state)
            await _sleep_or_stop(stop, delay)
        heartbeat_and_ping(STATE_STOPPED)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return


class HeartbeatFile:
    """Atomically writes the worker's last state so an external probe can read readiness.

    The temporary file is created with ``mkstemp`` (random name, ``O_EXCL``, never follows a
    pre-existing link) in the target directory, then renamed over the target. The heartbeat holds
    no secrets, so it is world-readable (0644) to let monitoring read it without the service user.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def write(self, state: str) -> None:
        payload = json.dumps(
            {"state": state, "updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid()}
        )
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(tmp_name, 0o644)
                os.replace(tmp_name, self._path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except OSError:
            # A heartbeat failure must never stop verification; the probe will report it stale.
            logger.warning("webhook_worker_heartbeat_write_failed")


def check_heartbeat(
    path: str, *, max_age_seconds: int, now: datetime | None = None
) -> tuple[bool, str]:
    """Readiness check: fresh heartbeat in an OK state. Returns (healthy, reason)."""
    current = now or datetime.now(UTC)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(data["updated_at"])
        state = str(data["state"])
    except (OSError, ValueError, KeyError, TypeError):
        return False, "no readable heartbeat"
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    age = (current - updated_at).total_seconds()
    if age > max_age_seconds:
        return False, f"heartbeat stale ({int(age)}s)"
    if state != STATE_OK:
        return False, f"not ready: {state}"
    return True, "ok"
