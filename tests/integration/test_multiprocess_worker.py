"""Real OS processes sharing one disposable PostgreSQL database.

Each worker is started with ``python -m tests.support.worker_process``, which runs the production
entry point with GitHub stubbed at the HTTP transport. Workers run from a temporary directory with
an explicit environment, so the developer's repository ``.env`` is never loaded.

Requires AKESIS_DISPOSABLE_TEST_DATABASE_URL pointing at a loopback akesis_test database.
Set AKESIS_EVIDENCE_DIR to keep the JSON evidence files produced by these tests.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.packages.database.models import WebhookEventModel
from src.packages.database.webhook_events import WebhookEventRepository

REPO = Path(__file__).resolve().parents[2]
PROCESS_TIMEOUT = 90.0


@pytest.fixture(scope="module")
def database_url() -> str:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL test database not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Refusing a database other than the explicitly designated local test database")
    return url


@pytest.fixture(scope="module")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await db.execute(delete(WebhookEventModel))
        await db.commit()
    yield maker
    await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"


async def _seed_due(
    sessions: async_sessionmaker[AsyncSession], delivery_id: str, installation_id: int
) -> None:
    async with sessions() as db:
        async with db.begin():
            await WebhookEventRepository(db).register(delivery_id, "a" * 64, "workflow_run")
    async with sessions() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.action = "completed"
        row.installation_id = installation_id
        row.event_repository_id = 101
        row.event_repository_name = "base/repo"
        row.run_id = time.time_ns() // 1000
        row.run_attempt = 1
        row.source_repository_id = 202
        row.source_repository_name = "fork/repo"
        row.source_sha = "c" * 40
        row.source_branch = "feature"
        row.status = "retryable_error"
        row.attempt_count = 1
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()


async def _state(sessions: async_sessionmaker[AsyncSession], delivery_id: str) -> dict[str, object]:
    async with sessions() as db:
        row = await db.get(WebhookEventModel, delivery_id, populate_existing=True)
    assert row is not None
    return {
        "delivery_id": delivery_id,
        "status": row.status,
        "attempt_count": row.attempt_count,
        "last_error_code": row.last_error_code,
        "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
    }


async def _settled(
    sessions: async_sessionmaker[AsyncSession], delivery_id: str, attempt: int
) -> bool:
    """True once the attempt has finished. Until then the claim is still ``verifying``."""
    state = await _state(sessions, delivery_id)
    return state["attempt_count"] == attempt and state["status"] != "verifying"


class WorkerFleet:
    """Starts and tracks real worker processes sharing one database and one GitHub log."""

    def __init__(self, tmp_path: Path, database_url: str, private_key_pem: str) -> None:
        self.tmp = tmp_path
        self.database_url = database_url
        self.private_key_pem = private_key_pem
        self.github_log = tmp_path / "github.jsonl"
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.logs: dict[str, Path] = {}

    def spawn(
        self,
        name: str,
        *,
        delay: float = 0.0,
        batch: int = 4,
        retry_after: str = "3600",
    ) -> subprocess.Popen[bytes]:
        log_path = self.tmp / f"{name}.log"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "PYTHONUNBUFFERED": "1",
            "ENVIRONMENT": "test",
            "DATABASE_URL": self.database_url,
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_PRIVATE_KEY": self.private_key_pem,
            "WORKER_POLL_SECONDS": "1",
            "WORKER_BATCH_SIZE": str(batch),
            "WORKER_DB_BACKOFF_MAX_SECONDS": "1",
            "WORKER_HEARTBEAT_PATH": str(self.tmp / f"{name}.heartbeat.json"),
            "AKESIS_TEST_GITHUB_LOG": str(self.github_log),
            "AKESIS_TEST_GITHUB_DELAY": str(delay),
            "AKESIS_TEST_GITHUB_RETRY_AFTER": retry_after,
        }
        handle = log_path.open("wb")
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.support.worker_process"],
            cwd=self.tmp,  # no repository .env is visible from here
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        handle.close()
        self.processes[name] = process
        self.logs[name] = log_path
        return process

    def github_records(self) -> list[dict[str, Any]]:
        if not self.github_log.exists():
            return []
        return [json.loads(line) for line in self.github_log.read_text().splitlines() if line]

    def installation_hits(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.github_records()
            if record["phase"] == "start"
            and record["method"] == "GET"
            and str(record["path"]).startswith("/app/installations/")
        ]

    def log_tails(self) -> str:
        parts = []
        for name, path in self.logs.items():
            tail = path.read_text(errors="replace")[-800:] if path.exists() else ""
            parts.append(f"--- {name} ---\n{tail}")
        return "\n".join(parts)


async def _wait_until(
    condition: Callable[[], Awaitable[bool]], fleet: WorkerFleet, timeout: float = PROCESS_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while not await condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not reached within {timeout}s\n{fleet.log_tails()}")
        await asyncio.sleep(0.2)


def _evidence(name: str, payload: dict[str, object]) -> None:
    directory = os.environ.get("AKESIS_EVIDENCE_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


@pytest.fixture
def fleet(tmp_path: Path, database_url: str, private_key_pem: str) -> Iterator[WorkerFleet]:
    workers = WorkerFleet(tmp_path, database_url, private_key_pem)
    yield workers
    for process in workers.processes.values():
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.asyncio
async def test_two_processes_share_due_work_without_duplicate_verification(
    sessions: async_sessionmaker[AsyncSession], fleet: WorkerFleet
) -> None:
    ids = [(_unique("mp-share"), 8000 + index) for index in range(12)]
    for delivery_id, installation_id in ids:
        await _seed_due(sessions, delivery_id, installation_id)

    worker_a = fleet.spawn("worker-a", delay=0.3)
    worker_b = fleet.spawn("worker-b", delay=0.3)

    async def all_verified() -> bool:
        for delivery_id, _ in ids:
            if not await _settled(sessions, delivery_id, 2):
                return False
        return True

    await _wait_until(all_verified, fleet)

    hits = Counter(str(record["path"]) for record in fleet.installation_hits())
    for _, installation_id in ids:
        assert hits[f"/app/installations/{installation_id}"] == 1, "duplicate GitHub verification"
    pids_that_verified = {record["pid"] for record in fleet.installation_hits()}
    assert pids_that_verified == {worker_a.pid, worker_b.pid}, (
        "work was not shared across processes"
    )
    assert worker_a.poll() is None and worker_b.poll() is None

    # SIGTERM on one worker must not disturb the other.
    worker_a.send_signal(signal.SIGTERM)
    assert worker_a.wait(timeout=30) == 0
    assert worker_b.poll() is None

    # Durable work continues on the surviving process.
    later = [(_unique("mp-later"), 8100 + index) for index in range(4)]
    for delivery_id, installation_id in later:
        await _seed_due(sessions, delivery_id, installation_id)

    async def later_verified() -> bool:
        for delivery_id, _ in later:
            if not await _settled(sessions, delivery_id, 2):
                return False
        return True

    await _wait_until(later_verified, fleet)
    later_pids = {
        record["pid"]
        for record in fleet.installation_hits()
        if str(record["path"]).startswith("/app/installations/81")
    }
    assert later_pids == {worker_b.pid}

    worker_b.send_signal(signal.SIGTERM)
    assert worker_b.wait(timeout=30) == 0

    states = [await _state(sessions, delivery_id) for delivery_id, _ in ids + later]
    _evidence(
        "two_process_distribution",
        {
            "worker_a_pid": worker_a.pid,
            "worker_b_pid": worker_b.pid,
            "pids_that_verified": sorted(pids_that_verified),
            "github_calls_per_installation": {k: v for k, v in sorted(hits.items())},
            "database_state": states,
        },
    )


@pytest.mark.asyncio
async def test_killed_worker_claim_is_recovered_only_after_the_stale_window(
    sessions: async_sessionmaker[AsyncSession], fleet: WorkerFleet
) -> None:
    delivery_id = _unique("mp-kill")
    installation_id = 8200
    await _seed_due(sessions, delivery_id, installation_id)

    victim = fleet.spawn("victim", delay=60.0, batch=1)

    async def in_flight() -> bool:
        return (
            any(record["pid"] == victim.pid for record in fleet.installation_hits())
            and (await _state(sessions, delivery_id))["status"] == "verifying"
        )

    await _wait_until(in_flight, fleet)
    victim.kill()  # SIGKILL: no shutdown path runs
    victim.wait(timeout=10)
    stranded = await _state(sessions, delivery_id)
    assert stranded["status"] == "verifying" and stranded["attempt_count"] == 2

    # A second process must not reclaim a live-looking claim before the stale window.
    rescuer = fleet.spawn("rescuer", delay=0.0, batch=1)
    await asyncio.sleep(3)
    assert (await _state(sessions, delivery_id))["attempt_count"] == 2
    assert not any(record["pid"] == rescuer.pid for record in fleet.installation_hits())

    # Simulate the stale window elapsing (ten real minutes are not practical in a test).
    async with sessions() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.updated_at = datetime.now(UTC) - timedelta(minutes=11)
        await db.commit()

    async def recovered() -> bool:
        return await _settled(sessions, delivery_id, 3)

    await _wait_until(recovered, fleet)
    recovered_state = await _state(sessions, delivery_id)
    pids = [record["pid"] for record in fleet.installation_hits()]
    assert pids == [victim.pid, rescuer.pid], "expected one killed attempt then one recovery"
    assert recovered_state["status"] == "retryable_error"

    rescuer.send_signal(signal.SIGTERM)
    assert rescuer.wait(timeout=30) == 0
    _evidence(
        "killed_worker_recovery",
        {
            "victim_pid": victim.pid,
            "victim_signal": "SIGKILL",
            "rescuer_pid": rescuer.pid,
            "github_request_pids_in_order": pids,
            "database_state_after_kill": stranded,
            "database_state_after_recovery": recovered_state,
            "note": "updated_at was moved back to simulate the 10-minute stale window elapsing.",
        },
    )


@pytest.mark.asyncio
async def test_sigterm_during_an_in_flight_verification_completes_it_and_exits_cleanly(
    sessions: async_sessionmaker[AsyncSession], fleet: WorkerFleet
) -> None:
    delivery_id = _unique("mp-term")
    await _seed_due(sessions, delivery_id, 8300)
    worker = fleet.spawn("terminated", delay=3.0, batch=1)

    async def in_flight() -> bool:
        return any(record["pid"] == worker.pid for record in fleet.installation_hits())

    await _wait_until(in_flight, fleet)
    worker.send_signal(signal.SIGTERM)  # arrives while the GitHub request is still held
    assert worker.wait(timeout=30) == 0

    final = await _state(sessions, delivery_id)
    assert final["status"] == "retryable_error" and final["attempt_count"] == 2
    heartbeat = json.loads((fleet.tmp / "terminated.heartbeat.json").read_text())
    assert heartbeat["state"] == "stopped"
    _evidence(
        "sigterm_in_flight",
        {"worker_pid": worker.pid, "exit_code": 0, "database_state": final},
    )


@pytest.mark.asyncio
async def test_worker_restart_resumes_durable_work_without_loss(
    sessions: async_sessionmaker[AsyncSession], fleet: WorkerFleet
) -> None:
    ids = [(_unique("mp-restart"), 8400 + index) for index in range(3)]
    for delivery_id, installation_id in ids:
        await _seed_due(sessions, delivery_id, installation_id)

    first = fleet.spawn("first-run", delay=0.0, batch=1)

    async def ready() -> bool:
        return (fleet.tmp / "first-run.heartbeat.json").exists()

    await _wait_until(ready, fleet)  # signal handlers are installed once the heartbeat exists
    first.send_signal(signal.SIGTERM)
    assert first.wait(timeout=30) == 0

    second = fleet.spawn("second-run", delay=0.0, batch=2)

    async def all_verified() -> bool:
        for delivery_id, _ in ids:
            if not await _settled(sessions, delivery_id, 2):
                return False
        return True

    await _wait_until(all_verified, fleet)
    hits = Counter(str(record["path"]) for record in fleet.installation_hits())
    assert all(hits[f"/app/installations/{iid}"] == 1 for _, iid in ids), "a delivery was repeated"
    second.send_signal(signal.SIGTERM)
    assert second.wait(timeout=30) == 0


@pytest.mark.asyncio
async def test_sigterm_with_a_queued_batch_releases_unstarted_claims_to_the_other_process(
    sessions: async_sessionmaker[AsyncSession], fleet: WorkerFleet
) -> None:
    """Two real processes: A is stopped mid-batch, B is running and takes the released claims."""
    ids = [(_unique("mp-queued"), 8500 + index) for index in range(4)]
    for delivery_id, installation_id in ids:
        await _seed_due(sessions, delivery_id, installation_id)

    # A starts alone so it claims the whole batch deterministically. Two workers polling at the same
    # moment can split a batch in any order, which would make this test depend on scheduling.
    worker_a = fleet.spawn("queued-a", delay=2.0, batch=4)

    async def a_in_flight() -> bool:
        return any(record["pid"] == worker_a.pid for record in fleet.installation_hits())

    await _wait_until(a_in_flight, fleet)
    worker_b = fleet.spawn("queued-b", delay=0.0, batch=4)  # starts while A still holds the batch
    worker_a.send_signal(signal.SIGTERM)
    assert worker_a.wait(timeout=60) == 0  # finishes its in-flight delivery, then stops

    async def all_settled() -> bool:
        for delivery_id, _ in ids:
            if not await _settled(sessions, delivery_id, 2):
                return False
        return True

    await _wait_until(all_settled, fleet)
    hits = Counter(str(record["path"]) for record in fleet.installation_hits())
    for _, installation_id in ids:
        assert hits[f"/app/installations/{installation_id}"] == 1, "a delivery was verified twice"
    pids = {record["pid"] for record in fleet.installation_hits()}
    assert worker_a.pid in pids and worker_b.pid in pids
    # A began exactly one delivery; the other three were released and taken by B.
    by_pid = Counter(record["pid"] for record in fleet.installation_hits())
    assert by_pid[worker_a.pid] == 1 and by_pid[worker_b.pid] == 3, dict(by_pid)
    states = [await _state(sessions, delivery_id) for delivery_id, _ in ids]
    assert all(state["attempt_count"] == 2 for state in states), "a released claim consumed budget"
    worker_b.send_signal(signal.SIGTERM)
    assert worker_b.wait(timeout=30) == 0
    _evidence(
        "sigterm_released_batch",
        {
            "worker_a_pid": worker_a.pid,
            "worker_b_pid": worker_b.pid,
            "github_calls_per_installation": dict(sorted(hits.items())),
            "database_state": states,
        },
    )
