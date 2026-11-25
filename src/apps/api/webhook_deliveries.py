"""Read-only operator visibility for webhook deliveries that are scheduled or exhausted.

The response model is the complete allow-list of exposed fields. It never includes payloads,
payload digests, signatures, request headers, installation identifiers, tokens or keys, so a
future column added to the table cannot leak into this response without changing this model.
"""

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import WebhookEventModel
from src.packages.database.session import get_db_session
from src.packages.database.webhook_events import WebhookEventRepository
from src.packages.shared.security import verify_read_token

router = APIRouter()

OperatorStatus = Literal["retryable_error", "exhausted", "provider_hold"]
DEFAULT_STATUSES: tuple[OperatorStatus, ...] = ("retryable_error", "provider_hold", "exhausted")


class WebhookDeliverySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delivery_id: str
    status: str
    failure_code: str | None
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None
    last_error_code: str | None
    received_at: datetime
    updated_at: datetime
    event_type: str


class WebhookDeliveryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[WebhookDeliverySummary]
    next_cursor: str | None


def _encode_cursor(updated_at: datetime, delivery_id: str) -> str:
    raw = json.dumps({"u": updated_at.isoformat(), "d": delivery_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        updated_at = datetime.fromisoformat(data["u"])
        delivery_id = str(data["d"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at, delivery_id


def _summary(row: WebhookEventModel, max_attempts: int) -> WebhookDeliverySummary:
    return WebhookDeliverySummary(
        delivery_id=row.delivery_id,
        status=row.status,
        failure_code=row.failure_code,
        attempt_count=row.attempt_count,
        max_attempts=max_attempts,
        next_retry_at=row.next_retry_at,
        last_error_code=row.last_error_code,
        received_at=row.received_at,
        updated_at=row.updated_at,
        event_type=row.event_type,
    )


@router.get(
    "/v1/webhook-deliveries",
    response_model=WebhookDeliveryPage,
    summary="List webhook deliveries that are scheduled for retry or exhausted (read-only)",
    dependencies=[Depends(verify_read_token)],
)
async def list_webhook_deliveries(
    request: Request,
    status: Annotated[list[OperatorStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> WebhookDeliveryPage:
    """Newest first, keyset-paginated on (updated_at, delivery_id).

    Repeat ``status`` to filter (for example ``?status=exhausted&status=retryable_error``). Omit it
    to list every operator-relevant state.
    """
    statuses: tuple[OperatorStatus, ...] = tuple(status) if status else DEFAULT_STATUSES
    after = _decode_cursor(cursor) if cursor else None
    max_attempts = request.app.state.settings.webhook_retry_max_attempts
    rows = list(
        await WebhookEventRepository(session).list_deliveries(
            statuses, cursor=after, limit=limit + 1
        )
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last.updated_at, last.delivery_id)
    return WebhookDeliveryPage(
        items=[_summary(row, max_attempts) for row in page],
        next_cursor=next_cursor,
    )
