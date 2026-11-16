import copy
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.apps.api.main import create_app
from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.database.session import get_db_session
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import FailureContext


@pytest.fixture
def payload() -> dict[str, Any]:
    repo = {"id": 1, "name": "repo", "full_name": "owner/repo", "owner": {"login": "owner"}}
    return {
        "action": "completed",
        "installation": {"id": 12},
        "repository": repo,
        "workflow_run": {
            "id": uuid.uuid4().int % (2**53),
            "run_attempt": 1,
            "head_repository": repo,
            "head_sha": "a" * 40,
            "head_branch": "main",
            "conclusion": "failure",
        },
    }


@pytest.fixture
async def app() -> AsyncIterator[tuple[Any, async_sessionmaker[AsyncSession]]]:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set AKESIS_DISPOSABLE_TEST_DATABASE_URL to run database provenance tests")
    parsed = urlsplit(url)
    # A configured URL must be a loopback akesis_test database; anything else fails closed.
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Explicit disposable local akesis_test database is required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    instance = create_app(
        Settings(environment="test", github_app_id=123, github_app_private_key=private)
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with factory() as db:
            yield db

    instance.dependency_overrides[get_db_session] = session
    try:
        yield instance, factory
    finally:
        await engine.dispose()


async def post(client: Any, payload: dict[str, Any], valid: bool = True) -> httpx.Response:
    body = json.dumps(payload).encode()
    signature = (
        "sha256="
        + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client), base_url="http://test"
    ) as http_client:
        return await http_client.post(
            "/v1/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": f"unit-{payload['workflow_run']['id']}",
                "X-Hub-Signature-256": signature if valid else "sha256=invalid",
            },
        )


def mock_api(
    api: respx.MockRouter,
    payload: dict[str, Any],
    authoritative: dict[str, Any] | None = None,
    include_source: bool = True,
    event_id: int = 1,
) -> None:
    run = copy.deepcopy(payload["workflow_run"]) if authoritative is None else authoritative
    run["repository"] = {**payload["repository"], "id": event_id}
    repo = payload["repository"]
    source = payload["workflow_run"]["head_repository"]
    api.get("https://api.github.com/app/installations/12").respond(
        200, json={"id": 12, "app_id": 123, "suspended_at": None}
    )
    api.post("https://api.github.com/app/installations/12/access_tokens").respond(
        201,
        json={
            "token": "synthetic",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    api.get("https://api.github.com/installation/repositories").respond(
        200, json={"repositories": [repo, source] if include_source else [repo]}
    )
    api.get(
        f"https://api.github.com/repos/owner/repo/actions/runs/{payload['workflow_run']['id']}"
    ).respond(200, json=run)
    api.get(f"https://api.github.com/repos/{source['full_name']}").respond(200, json=source)
    api.get(f"https://api.github.com/repos/{source['full_name']}/commits/{'a' * 40}").respond(
        200, json={"sha": "a" * 40}
    )


@pytest.mark.asyncio
@respx.mock
async def test_signed_webhook_persists_verified_provenance_without_remediation(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    client, persist = app
    mock_api(respx.mock, payload)
    response = await post(client, payload)
    assert response.status_code == 202
    async with persist() as db:
        record = (
            await db.execute(
                select(PipelineModel).where(
                    PipelineModel.incident_id == response.json()["incident_id"]
                )
            )
        ).scalar_one()
    assert record.failure_context_json is not None
    context = FailureContext.model_validate_json(record.failure_context_json)
    assert context.provenance is not None and context.provenance.source_repository.id == 1
    assert record.status == "failed"


@pytest.mark.asyncio
@respx.mock
async def test_fork_source_is_separate(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    payload["workflow_run"]["head_repository"] = {"id": 2, "full_name": "fork/repo"}
    mock_api(respx.mock, payload)
    client, persist = app
    response = await post(client, payload)
    assert response.status_code == 202
    async with persist() as db:
        record = (
            await db.execute(
                select(PipelineModel).where(
                    PipelineModel.incident_id == response.json()["incident_id"]
                )
            )
        ).scalar_one()
    assert record.failure_context_json is not None
    context = FailureContext.model_validate_json(record.failure_context_json)
    assert context.provenance is not None and context.provenance.event_repository.id == 1
    assert context.provenance.source_repository.id == 2


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 99),
        ("run_attempt", 2),
        ("head_sha", "b" * 40),
        ("head_branch", "other"),
        ("head_repository", {"id": 99, "full_name": "fork/repo"}),
    ],
)
async def test_authoritative_mismatch_fails_closed(
    app: tuple[Any, async_sessionmaker[AsyncSession]],
    payload: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    run = copy.deepcopy(payload["workflow_run"])
    run[field] = value
    mock_api(respx.mock, payload, run)
    client, persist = app
    assert (await post(client, payload)).status_code == 422
    async with persist() as db:
        events = (
            (
                await db.execute(
                    select(WebhookEventModel).where(
                        WebhookEventModel.delivery_id == f"unit-{payload['workflow_run']['id']}"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1 and events[0].status == "rejected" and events[0].pipeline_id is None


@pytest.mark.asyncio
@respx.mock
async def test_invalid_signature_stops_before_api(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    client, persist = app
    assert (await post(client, payload, valid=False)).status_code == 401
    async with persist() as db:
        events = (
            (
                await db.execute(
                    select(WebhookEventModel).where(
                        WebhookEventModel.delivery_id == f"unit-{payload['workflow_run']['id']}"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert not events
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_missing_head_repository_fails_closed(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    payload["workflow_run"]["head_repository"] = None
    client, persist = app
    assert (await post(client, payload)).status_code == 422
    async with persist() as db:
        events = (
            (
                await db.execute(
                    select(WebhookEventModel).where(
                        WebhookEventModel.delivery_id == f"unit-{payload['workflow_run']['id']}"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1 and events[0].status == "rejected" and events[0].pipeline_id is None
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_inaccessible_fork_does_not_fall_back(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    payload["workflow_run"]["head_repository"] = {"id": 2, "full_name": "fork/repo"}
    mock_api(respx.mock, payload, include_source=False)
    client, persist = app
    assert (await post(client, payload)).status_code == 422
    async with persist() as db:
        events = (
            (
                await db.execute(
                    select(WebhookEventModel).where(
                        WebhookEventModel.delivery_id == f"unit-{payload['workflow_run']['id']}"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1 and events[0].status == "rejected" and events[0].pipeline_id is None


@pytest.mark.asyncio
@respx.mock
async def test_mismatched_event_repository_id_fails_closed(
    app: tuple[Any, async_sessionmaker[AsyncSession]], payload: dict[str, Any]
) -> None:
    mock_api(respx.mock, payload, event_id=999)
    client, persist = app
    assert (await post(client, payload)).status_code == 422
    async with persist() as db:
        events = (
            (
                await db.execute(
                    select(WebhookEventModel).where(
                        WebhookEventModel.delivery_id == f"unit-{payload['workflow_run']['id']}"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1 and events[0].status == "rejected" and events[0].pipeline_id is None


def test_historical_context_without_provenance_is_unverified() -> None:
    context = FailureContext.model_validate(
        {
            "incident_id": "old",
            "repository_owner": "owner",
            "repository_name": "repo",
            "run_id": 1,
            "workflow_name": "CI",
            "commit_sha": "a" * 40,
            "branch": "main",
            "run_url": "",
            "conclusion": "failure",
            "signal": {},
        }
    )
    assert context.provenance is None
