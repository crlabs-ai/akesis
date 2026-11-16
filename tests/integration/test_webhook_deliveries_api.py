"""Operator visibility endpoint: authorization, filtering, pagination and data leakage.

Requires AKESIS_DISPOSABLE_TEST_DATABASE_URL pointing at a loopback akesis_test database.
"""

import base64
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.apps.api.main import create_app
from src.packages.database.models import WebhookEventModel
from src.packages.database.session import get_db_session
from src.packages.database.webhook_events import WebhookEventRepository
from src.packages.shared.config import Settings

READ_TOKEN = "synthetic-read-token-0123456789abcdef"
PAYLOAD_DIGEST = "d1g3st" * 10 + "abcd"  # synthetic, 64 characters
SECRET_INSTALLATION_ID = 987_654_321
SECRET_SOURCE_SHA = "9" * 40
EXPECTED_FIELDS = {
    "delivery_id",
    "status",
    "failure_code",
    "attempt_count",
    "max_attempts",
    "next_retry_at",
    "last_error_code",
    "received_at",
    "updated_at",
    "event_type",
}


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("AKESIS_DISPOSABLE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL test database not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/akesis_test":
        pytest.fail("Refusing a database other than the explicitly designated local test database")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        await db.execute(delete(WebhookEventModel))
        await db.commit()
    yield sessions
    await engine.dispose()


def _app(database: async_sessionmaker[AsyncSession], **overrides: object) -> FastAPI:
    values: dict[str, object] = {
        "environment": "test",
        "read_api_token": READ_TOKEN,
        "github_app_id": 123,
    }
    values.update(overrides)
    app = create_app(Settings(**values))  # type: ignore[arg-type]

    async def session() -> AsyncIterator[AsyncSession]:
        async with database() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    return app


async def _seed(
    database: async_sessionmaker[AsyncSession],
    delivery_id: str,
    *,
    status: str,
    updated_at: datetime | None = None,
    **values: object,
) -> None:
    async with database() as db:
        async with db.begin():
            await WebhookEventRepository(db).register(delivery_id, PAYLOAD_DIGEST, "workflow_run")
    async with database() as db:
        row = await db.get(WebhookEventModel, delivery_id)
        assert row is not None
        row.status = status
        row.attempt_count = 2
        row.installation_id = SECRET_INSTALLATION_ID
        row.source_sha = SECRET_SOURCE_SHA
        row.source_repository_name = "fork/repo"
        row.workflow_name = "Sensitive Workflow Name"
        row.run_url = "https://github.com/base/repo/actions/runs/1"
        row.last_error_code = "github_rate_limited"
        row.failure_code = "github_rate_limited"
        row.next_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        if updated_at is not None:
            row.updated_at = updated_at
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


def _headers(token: str | None = READ_TOKEN) -> dict[str, str]:
    return {"X-Akesis-Read-Token": token} if token else {}


async def _get(
    app: FastAPI,
    params: dict[str, str | int | list[str]] | None = None,
    token: str | None = READ_TOKEN,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.get("/v1/webhook-deliveries", params=params, headers=_headers(token))


# --- Authorization --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_token_is_rejected(database: async_sessionmaker[AsyncSession]) -> None:
    response = await _get(_app(database), token=None)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_is_rejected(database: async_sessionmaker[AsyncSession]) -> None:
    response = await _get(_app(database), token="not-the-token")
    assert response.status_code == 401
    assert READ_TOKEN not in response.text


@pytest.mark.asyncio
async def test_correct_token_is_accepted(database: async_sessionmaker[AsyncSession]) -> None:
    response = await _get(_app(database))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_production_without_configured_token_fails_closed(
    database: async_sessionmaker[AsyncSession],
) -> None:
    # Production Settings cannot be constructed without every production secret, so the app is
    # built in test mode and only the settings object the auth check reads is production-like.
    app = _app(database)
    app.state.settings = SimpleNamespace(environment="production", read_api_token="")
    response = await _get(app, token=None)
    assert response.status_code == 401


# --- Filtering and validation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_listing_includes_only_operator_states(
    database: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(database, "f-retry", status="retryable_error")
    await _seed(database, "f-hold", status="provider_hold")
    await _seed(database, "f-dead", status="exhausted")
    await _seed(database, "f-ok", status="verified")
    await _seed(database, "f-rej", status="rejected")

    body = (await _get(_app(database))).json()
    ids = {item["delivery_id"] for item in body["items"]}
    assert ids == {"f-retry", "f-hold", "f-dead"}


@pytest.mark.asyncio
async def test_status_filter_restricts_results(database: async_sessionmaker[AsyncSession]) -> None:
    await _seed(database, "s-retry", status="retryable_error")
    await _seed(database, "s-dead", status="exhausted")
    body = (await _get(_app(database), {"status": "exhausted"})).json()
    assert [item["delivery_id"] for item in body["items"]] == ["s-dead"]


@pytest.mark.asyncio
async def test_repeated_status_filter_combines_values(
    database: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(database, "c-retry", status="retryable_error")
    await _seed(database, "c-dead", status="exhausted")
    await _seed(database, "c-hold", status="provider_hold")
    body = (await _get(_app(database), {"status": ["exhausted", "retryable_error"]})).json()
    assert {item["delivery_id"] for item in body["items"]} == {"c-retry", "c-dead"}


@pytest.mark.asyncio
async def test_unknown_status_is_rejected(database: async_sessionmaker[AsyncSession]) -> None:
    assert (await _get(_app(database), {"status": "verified"})).status_code == 422
    assert (await _get(_app(database), {"status": "verifying"})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 201, -1, "abc"])
async def test_out_of_range_limit_is_rejected(
    database: async_sessionmaker[AsyncSession], limit: str | int
) -> None:
    assert (await _get(_app(database), {"limit": limit})).status_code == 422


@pytest.mark.asyncio
async def test_malformed_cursor_is_rejected(database: async_sessionmaker[AsyncSession]) -> None:
    for cursor in ("!!!", "bm90LWpzb24", base64.urlsafe_b64encode(b'{"x":1}').decode()):
        response = await _get(_app(database), {"cursor": cursor})
        assert response.status_code == 422, cursor


# --- Keyset pagination ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyset_pages_cover_every_row_once_in_descending_order(
    database: async_sessionmaker[AsyncSession],
) -> None:
    base = datetime.now(UTC) - timedelta(hours=1)
    seeded: set[str] = set()
    for index in range(7):
        delivery_id = f"p-{index}"
        seeded.add(delivery_id)
        # Two rows share a timestamp to exercise the delivery_id tie-breaker.
        stamp = base + timedelta(seconds=index // 2)
        await _seed(database, delivery_id, status="retryable_error", updated_at=stamp)

    app = _app(database)
    seen: list[tuple[str, str]] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int | list[str]] = {"limit": 2, "status": "retryable_error"}
        if cursor:
            params["cursor"] = cursor
        body = (await _get(app, params)).json()
        pages += 1
        assert len(body["items"]) <= 2
        seen.extend((item["updated_at"], item["delivery_id"]) for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, "pagination did not terminate"

    ids = [delivery_id for _, delivery_id in seen]
    assert len(ids) == len(set(ids)) == 7
    assert set(ids) == seeded
    keys = [(stamp, delivery_id) for stamp, delivery_id in seen]
    assert keys == sorted(keys, reverse=True), "pages are not newest-first"


@pytest.mark.asyncio
async def test_next_cursor_is_absent_when_everything_fits(
    database: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(database, "only-one", status="exhausted")
    body = (await _get(_app(database), {"status": "exhausted", "limit": 5})).json()
    assert body["next_cursor"] is None
    assert [item["delivery_id"] for item in body["items"]] == ["only-one"]


# --- Data leakage ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_exposes_exactly_the_approved_fields(
    database: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(database, "leak-check", status="exhausted")
    response = await _get(_app(database), {"status": "exhausted"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == EXPECTED_FIELDS
    assert item["max_attempts"] == 5
    assert item["last_error_code"] == "github_rate_limited"


@pytest.mark.asyncio
async def test_response_never_contains_sensitive_material(
    database: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(database, "leak-scan", status="retryable_error")
    await _seed(database, "leak-scan-2", status="exhausted")
    response = await _get(_app(database))
    text = response.text
    forbidden = [
        PAYLOAD_DIGEST,
        str(SECRET_INSTALLATION_ID),
        SECRET_SOURCE_SHA,
        "Sensitive Workflow Name",
        "fork/repo",
        "run_url",
        "https://github.com",
        "payload",
        "signature",
        "installation",
        "token",
        "private",
        "Authorization",
        READ_TOKEN,
    ]
    for needle in forbidden:
        assert needle not in text, f"sensitive value leaked: {needle!r}"
    body = json.loads(text)
    for item in body["items"]:
        assert set(item) == EXPECTED_FIELDS


@pytest.mark.asyncio
async def test_error_responses_do_not_echo_the_submitted_token(
    database: async_sessionmaker[AsyncSession],
) -> None:
    submitted = "attacker-supplied-token-value"
    response = await _get(_app(database), token=submitted)
    assert response.status_code == 401
    assert submitted not in response.text


def test_cursor_round_trip_preserves_position() -> None:
    from src.apps.api.webhook_deliveries import _decode_cursor, _encode_cursor

    stamp = datetime(2026, 11, 16, 12, 0, 0, 123456, tzinfo=UTC)
    cursor = _encode_cursor(stamp, "delivery-x")
    assert _decode_cursor(cursor) == (stamp, "delivery-x")
