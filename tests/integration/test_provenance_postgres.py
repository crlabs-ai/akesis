"""Webhook persistence integration; requires an explicitly designated disposable database."""

import asyncio
import copy
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.apps.api.main import create_app
from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.database.repositories import PipelineRepository
from src.packages.database.session import get_db_session
from src.packages.database.webhook_events import DeliveryConflictError, WebhookEventRepository
from src.packages.sdk.github_app_auth import GitHubAppAuth
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import FailureContext, PipelineStatus
from src.packages.shared.provenance_recovery import recover_historical_provenance
from src.packages.shared.workflow_provenance import WorkflowRunProvenanceVerifier


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL test database not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Refusing a database other than the explicitly designated local test database")
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
@respx.mock
async def test_signed_event_round_trips_through_postgres(
    database: async_sessionmaker[AsyncSession],
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    app = create_app(
        Settings(environment="test", github_app_id=123, github_app_private_key=private)
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with database() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    event_repo = {"id": 101, "full_name": "base/repo", "name": "repo", "owner": {"login": "base"}}
    source_repo = {"id": 202, "full_name": "fork/repo"}
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    run = {
        "id": run_id,
        "run_attempt": 2,
        "head_repository": source_repo,
        "head_sha": "a" * 40,
        "head_branch": "feature",
        "conclusion": "failure",
    }
    payload = {
        "action": "completed",
        "installation": {"id": 404},
        "repository": event_repo,
        "workflow_run": run,
    }
    installation_api = respx.get("https://api.github.com/app/installations/404").respond(
        200, json={"id": 404, "app_id": 123, "suspended_at": None}
    )
    respx.post("https://api.github.com/app/installations/404/access_tokens").respond(
        201,
        json={
            "token": "synthetic",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    respx.get("https://api.github.com/installation/repositories").respond(
        200, json={"repositories": [event_repo, source_repo]}
    )
    respx.get(f"https://api.github.com/repos/base/repo/actions/runs/{run_id}").respond(
        200, json={**run, "repository": event_repo}
    )
    respx.get("https://api.github.com/repos/fork/repo").respond(200, json=source_repo)
    respx.get(f"https://api.github.com/repos/fork/repo/commits/{'a' * 40}").respond(
        200, json={"sha": "a" * 40}
    )
    body = json.dumps(payload).encode()
    signature = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": f"test-delivery-{run_id}-2",
        }
        concurrent = await asyncio.gather(
            client.post("/v1/webhooks/github", content=body, headers=headers),
            client.post("/v1/webhooks/github", content=body, headers=headers),
            client.post(
                "/v1/webhooks/github",
                content=body,
                headers={**headers, "X-GitHub-Delivery": f"test-other-{run_id}-2"},
            ),
        )
    assert all(item.status_code in (202, 503) for item in concurrent)
    accepted = [
        item for item in concurrent if item.status_code == 202 and item.json().get("incident_id")
    ]
    assert len(accepted) == 1
    response = accepted[0]
    assert response.status_code == 202
    async with database() as db:
        pipeline = await PipelineRepository(db).get_by_incident_id(response.json()["incident_id"])
    assert pipeline is not None and pipeline.status == PipelineStatus.FAILED
    assert pipeline.failure_context_json is not None
    context = FailureContext.model_validate_json(pipeline.failure_context_json)
    assert context.provenance is not None
    assert context.provenance.event_repository.id == 101
    assert context.provenance.event_repository.full_name == "base/repo"
    assert context.provenance.source_repository.id == 202
    assert context.provenance.source_repository.full_name == "fork/repo"
    assert (context.provenance.run_id, context.provenance.run_attempt) == (run_id, 2)
    assert (context.provenance.head_sha, context.provenance.head_branch) == ("a" * 40, "feature")
    assert context.provenance.installation_id == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def deliver(delivery_id: str) -> httpx.Response:
            return await client.post(
                "/v1/webhooks/github",
                content=body,
                headers={
                    "X-GitHub-Event": "workflow_run",
                    "X-Hub-Signature-256": signature,
                    "X-GitHub-Delivery": delivery_id,
                },
            )

        responses = await asyncio.gather(
            deliver(f"test-delivery-{run_id}-2"), deliver(f"test-other-{run_id}-2")
        )
    assert all(item.status_code == 202 for item in responses)
    async with database() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(PipelineModel)
                .where(PipelineModel.run_id == run_id)
            )
        ).scalar_one()
        events = (
            (await db.execute(select(WebhookEventModel).where(WebhookEventModel.run_id == run_id)))
            .scalars()
            .all()
        )
    assert count == 1
    assert len(events) == 2
    assert {item.status for item in events} == {"verified", "duplicate"}
    assert all(item.pipeline_id == pipeline.pipeline_id for item in events)

    missing_payload: dict[str, Any] = copy.deepcopy(payload)
    missing_payload["workflow_run"]["head_repository"] = None
    missing_body = json.dumps(missing_payload).encode()
    missing_signature = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.encode(), missing_body, hashlib.sha256
        ).hexdigest()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.post(
            "/v1/webhooks/github",
            content=missing_body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": missing_signature,
                "X-GitHub-Delivery": f"test-missing-{run_id}",
            },
        )
        assert missing.status_code == 422
        installation_api.respond(503)
        headers = {
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": f"test-retry-{run_id}",
        }
        temporary = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert temporary.status_code == 503
        installation_api.respond(200, json={"id": 404, "app_id": 123, "suspended_at": None})
        # The transient failure schedules a backoff; an immediate redelivery must wait for it.
        too_early = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert too_early.status_code == 503
        async with database() as db:
            scheduled = await db.get(WebhookEventModel, f"test-retry-{run_id}")
            assert scheduled is not None and scheduled.next_retry_at is not None
            scheduled.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        retried = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert retried.status_code == 202
    async with database() as db:
        missing_event = await db.get(WebhookEventModel, f"test-missing-{run_id}")
        retry_event = await db.get(WebhookEventModel, f"test-retry-{run_id}")
    assert missing_event is not None and missing_event.status == "rejected"
    assert missing_event.source_repository_id is None and missing_event.pipeline_id is None
    assert retry_event is not None and retry_event.status == "duplicate"
    assert retry_event.attempt_count == 2
    # Simulate a historical pipeline with a trustworthy linked event but no saved provenance.
    context.provenance = None
    async with database() as db:
        stored = await db.get(PipelineModel, pipeline.pipeline_id)
        assert stored is not None
        stored.failure_context_json = context.model_dump_json()
        await db.commit()
    async with httpx.AsyncClient(base_url="https://api.github.com") as api:
        verifier = WorkflowRunProvenanceVerifier(GitHubAppAuth(123, private, client=api), api)
        async with database() as db:
            assert await recover_historical_provenance(db, pipeline.pipeline_id, verifier)
            await db.commit()
        async with database() as db:
            assert await recover_historical_provenance(db, pipeline.pipeline_id, verifier)
            await db.commit()
            recovered = await db.get(PipelineModel, pipeline.pipeline_id)
            assert recovered is not None and recovered.failure_context_json is not None
            assert (
                FailureContext.model_validate_json(recovered.failure_context_json).provenance
                is not None
            )
            saved_context = recovered.failure_context_json
            altered = FailureContext.model_validate_json(saved_context)
            assert altered.provenance is not None
            altered.provenance = altered.provenance.model_copy(update={"head_sha": "b" * 40})
            recovered.failure_context_json = altered.model_dump_json()
            await db.commit()
        async with database() as db:
            assert not await recover_historical_provenance(db, pipeline.pipeline_id, verifier)
            recovered = await db.get(PipelineModel, pipeline.pipeline_id)
            assert recovered is not None
            recovered.failure_context_json = saved_context
            await db.commit()
        legacy_id = f"legacy-{run_id}"
        legacy_context = context.model_copy(update={"incident_id": legacy_id, "provenance": None})
        async with database() as db:
            db.add(
                PipelineModel(
                    pipeline_id=legacy_id,
                    incident_id=legacy_id,
                    repository_owner="fork",
                    repository_name="repo",
                    run_id=run_id,
                    commit_sha="a" * 40,
                    status="failed",
                    failure_context_json=legacy_context.model_dump_json(),
                )
            )
            await db.commit()
        async with database() as db:
            assert not await recover_historical_provenance(db, legacy_id, verifier)
            stored_legacy = await db.get(PipelineModel, legacy_id)
            assert stored_legacy is not None and stored_legacy.failure_context_json is not None
            assert (
                FailureContext.model_validate_json(stored_legacy.failure_context_json).provenance
                is None
            )
            # A plausible provenance object alone does not establish a verified event link.
            assert recovered is not None and recovered.failure_context_json is not None
            recovered_context = FailureContext.model_validate_json(recovered.failure_context_json)
            legacy_context.provenance = recovered_context.provenance
            stored_legacy.failure_context_json = legacy_context.model_dump_json()
            await db.commit()
        async with database() as db:
            assert not await recover_historical_provenance(db, legacy_id, verifier)


@pytest.mark.asyncio
async def test_delivery_registration_and_claim_are_atomic(
    database: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = f"test-{datetime.now(UTC).timestamp()}"

    async def register() -> bool:
        async with database() as db:
            async with db.begin():
                _, created = await WebhookEventRepository(db).register(
                    delivery_id, "a" * 64, "workflow_run"
                )
                return created

    assert sorted(await asyncio.gather(register(), register())) == [False, True]

    async def claim() -> bool:
        async with database() as db:
            async with db.begin():
                return await WebhookEventRepository(db).claim(delivery_id)

    assert sorted(await asyncio.gather(claim(), claim())) == [False, True]
    async with database() as db:
        async with db.begin():
            with pytest.raises(DeliveryConflictError):
                await WebhookEventRepository(db).register(delivery_id, "b" * 64, "workflow_run")


@pytest.mark.asyncio
async def test_signed_malformed_delivery_is_recorded_without_payload(
    database: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(Settings(environment="test"))

    async def session() -> AsyncIterator[AsyncSession]:
        async with database() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    body = b"{malformed-synthetic-json"
    digest = hashlib.sha256(body).hexdigest()
    signature = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    delivery_id = f"malformed-{int(datetime.now(UTC).timestamp() * 1_000_000)}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": delivery_id,
        }
        assert (
            await client.post("/v1/webhooks/github", content=body, headers=headers)
        ).status_code == 400
        headers["X-Hub-Signature-256"] = "sha256=invalid"
        headers["X-GitHub-Delivery"] = delivery_id + "-invalid"
        assert (
            await client.post("/v1/webhooks/github", content=body, headers=headers)
        ).status_code == 401
    async with database() as db:
        event = await db.get(WebhookEventModel, delivery_id)
        assert event is not None and event.status == "rejected"
        assert event.failure_code == "malformed_json" and event.payload_sha256 == digest
        assert (
            event.run_id is None
            and event.source_repository_id is None
            and event.pipeline_id is None
        )
        assert await db.get(WebhookEventModel, delivery_id + "-invalid") is None


@pytest.mark.asyncio
@respx.mock
async def test_rate_limited_delivery_is_scheduled_and_not_reclaimed_early(
    database: async_sessionmaker[AsyncSession],
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    app = create_app(
        Settings(environment="test", github_app_id=123, github_app_private_key=private)
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with database() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    run_id = int(datetime.now(UTC).timestamp() * 1_000_000)
    delivery_id = f"test-ratelimit-{run_id}"
    payload = {
        "action": "completed",
        "installation": {"id": 405},
        "repository": {
            "id": 101,
            "full_name": "base/repo",
            "name": "repo",
            "owner": {"login": "base"},
        },
        "workflow_run": {
            "id": run_id,
            "run_attempt": 1,
            "head_sha": "b" * 40,
            "head_branch": "feature",
            "head_repository": {"id": 202, "full_name": "fork/repo"},
            "conclusion": "failure",
        },
    }
    installation_api = respx.get("https://api.github.com/app/installations/405").respond(
        429, headers={"retry-after": "120"}
    )
    body = json.dumps(payload).encode()
    signature = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert first.status_code == 503
        assert installation_api.call_count == 1

        async with database() as db:
            event = await db.get(WebhookEventModel, delivery_id)
        assert event is not None
        assert event.status == "retryable_error"
        assert event.attempt_count == 1
        assert event.next_retry_at is not None
        scheduled = event.next_retry_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        delay = (scheduled - datetime.now(UTC)).total_seconds()
        assert 100 <= delay <= 125

        # An early redelivery must not be claimed or re-verified against GitHub.
        early = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert early.status_code == 503
        assert installation_api.call_count == 1
        async with database() as db:
            still_waiting = await db.get(WebhookEventModel, delivery_id)
        assert still_waiting is not None and still_waiting.attempt_count == 1

        # Once the scheduled time has passed, the retry is claimed and verification runs again.
        async with database() as db:
            row = await db.get(WebhookEventModel, delivery_id)
            assert row is not None
            row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert installation_api.call_count == 2
        async with database() as db:
            retried = await db.get(WebhookEventModel, delivery_id)
        assert retried is not None and retried.attempt_count == 2
