"""Atomic PostgreSQL operations for durable webhook deliveries."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import WebhookEventModel

EXHAUSTED_FAILURE_CODE = "verification_retries_exhausted"
# Scheduled states: retryable_error waits for backoff; provider_hold waits for a provider-directed
# delay longer than the sanity ceiling. Both become claimable once due.
SCHEDULED_STATUSES = ("retryable_error", "provider_hold")
STALE_VERIFICATION_WINDOW = timedelta(minutes=10)


class DeliveryConflictError(ValueError):
    """An existing unsigned delivery header was reused with different signed content."""


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(
        self, delivery_id: str, payload_sha256: str, event_type: str
    ) -> tuple[WebhookEventModel, bool]:
        """Register once; a delivery header is never proof of payload identity.

        The caller owns the transaction and must authenticate before invoking this method.
        """
        now = datetime.now(UTC)
        stmt = (
            insert(WebhookEventModel)
            .values(
                delivery_id=delivery_id,
                payload_sha256=payload_sha256,
                event_type=event_type,
                status="received",
                attempt_count=0,
                received_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEventModel.delivery_id])
            .returning(WebhookEventModel)
        )
        inserted = (await self.session.execute(stmt)).scalar_one_or_none()
        if inserted is not None:
            return inserted, True
        existing = (
            await self.session.execute(
                select(WebhookEventModel).where(WebhookEventModel.delivery_id == delivery_id)
            )
        ).scalar_one()
        if existing.payload_sha256 != payload_sha256 or existing.event_type != event_type:
            raise DeliveryConflictError("Delivery identifier conflicts with a prior signed payload")
        return existing, False

    async def state(self, delivery_id: str) -> WebhookEventModel | None:
        """Read the current row for an authenticated delivery (no state change).

        ``populate_existing`` refreshes any copy this session already holds, because bulk
        UPDATE statements do not update the identity map.
        """
        return await self.session.get(WebhookEventModel, delivery_id, populate_existing=True)

    async def select_due(self, limit: int) -> Sequence[WebhookEventModel]:
        """Lock a batch of deliveries that need worker action, skipping rows other workers hold.

        Due means a scheduled state whose time has come, or a verifying claim that went stale.
        The rows are only locked for the caller's transaction; each is then claimed or exhausted
        with the atomic transitions below, which remain the authority.
        """
        now = datetime.now(UTC)
        stale_before = now - STALE_VERIFICATION_WINDOW
        stmt = (
            select(WebhookEventModel)
            .where(
                or_(
                    and_(
                        WebhookEventModel.status.in_(SCHEDULED_STATUSES),
                        or_(
                            WebhookEventModel.next_retry_at.is_(None),
                            WebhookEventModel.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        WebhookEventModel.status == "verifying",
                        WebhookEventModel.updated_at < stale_before,
                    ),
                )
            )
            .order_by(WebhookEventModel.updated_at, WebhookEventModel.delivery_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def claim(self, delivery_id: str, max_attempts: int | None = None) -> bool:
        """Atomically claim a received or due scheduled event for a single verifier.

        With ``max_attempts``, an event whose attempt budget is spent is not claimable here;
        ``exhaust`` performs that terminal transition. A refused claim never increments
        ``attempt_count``.
        """
        now = datetime.now(UTC)
        stale_before = now - STALE_VERIFICATION_WINDOW
        within_budget = (
            WebhookEventModel.attempt_count < max_attempts if max_attempts is not None else true()
        )
        stmt = (
            update(WebhookEventModel)
            .where(
                WebhookEventModel.delivery_id == delivery_id,
                or_(
                    WebhookEventModel.status == "received",
                    and_(
                        WebhookEventModel.status.in_(SCHEDULED_STATUSES),
                        or_(
                            WebhookEventModel.next_retry_at.is_(None),
                            WebhookEventModel.next_retry_at <= now,
                        ),
                        within_budget,
                    ),
                    and_(
                        WebhookEventModel.status == "verifying",
                        WebhookEventModel.updated_at < stale_before,
                        within_budget,
                    ),
                ),
            )
            .values(
                status="verifying",
                attempt_count=WebhookEventModel.attempt_count + 1,
                updated_at=now,
                next_retry_at=None,
            )
            .returning(WebhookEventModel.delivery_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def release_unstarted(self, delivery_id: str, attempt_number: int) -> bool:
        """Return a claim that was never started to the schedule, without consuming its attempt.

        Used during shutdown for deliveries claimed in a batch but not yet verified. The guard
        (``verifying`` with this attempt number) means the transition touches only the claim this
        worker still owns. The attempt is undone because no request for it was ever sent. The row
        becomes due immediately, so another worker can claim it.
        """
        now = datetime.now(UTC)
        stmt = (
            update(WebhookEventModel)
            .where(
                WebhookEventModel.delivery_id == delivery_id,
                WebhookEventModel.status == "verifying",
                WebhookEventModel.attempt_count == attempt_number,
            )
            .values(
                status="retryable_error",
                attempt_count=WebhookEventModel.attempt_count - 1,
                next_retry_at=now,
                updated_at=now,
            )
            .returning(WebhookEventModel.delivery_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def exhaust(self, delivery_id: str, max_attempts: int) -> bool:
        """Move an event whose attempt budget is spent to the terminal ``exhausted`` state.

        Only a scheduled event, or a stale verifying claim, with no attempts left qualifies.
        The transition is conditional, so concurrent callers produce a single change. The most
        recent transient cause is kept in ``last_error_code``.
        """
        now = datetime.now(UTC)
        stale_before = now - STALE_VERIFICATION_WINDOW
        stmt = (
            update(WebhookEventModel)
            .where(
                WebhookEventModel.delivery_id == delivery_id,
                WebhookEventModel.attempt_count >= max_attempts,
                or_(
                    WebhookEventModel.status.in_(SCHEDULED_STATUSES),
                    and_(
                        WebhookEventModel.status == "verifying",
                        WebhookEventModel.updated_at < stale_before,
                    ),
                ),
            )
            .values(
                status="exhausted",
                failure_code=EXHAUSTED_FAILURE_CODE,
                next_retry_at=None,
                updated_at=now,
            )
            .returning(WebhookEventModel.delivery_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def record_attempt_failure(
        self,
        delivery_id: str,
        attempt_number: int,
        *,
        retryable: bool,
        failure_code: str,
        next_retry_at: datetime | None,
        max_attempts: int,
        retry_status: str = "retryable_error",
    ) -> str | None:
        """Persist the outcome of a failed attempt and return the resulting status.

        Only the holder of the current attempt may write (guarded by ``attempt_count``). A
        retryable failure on the last permitted attempt is terminal and becomes ``exhausted``.
        ``retry_status`` selects ``provider_hold`` for provider delays beyond the ceiling.
        Returns None when the attempt was superseded and nothing changed.
        """
        if retry_status not in SCHEDULED_STATUSES:
            raise ValueError("retry_status must be a scheduled status")
        if not retryable:
            status, code, due = "rejected", failure_code, None
        elif attempt_number >= max_attempts:
            status, code, due = "exhausted", EXHAUSTED_FAILURE_CODE, None
        else:
            status, code, due = retry_status, failure_code, next_retry_at
        stmt = (
            update(WebhookEventModel)
            .where(
                WebhookEventModel.delivery_id == delivery_id,
                WebhookEventModel.status == "verifying",
                WebhookEventModel.attempt_count == attempt_number,
            )
            .values(
                status=status,
                failure_code=code,
                last_error_code=failure_code,
                next_retry_at=due,
                updated_at=datetime.now(UTC),
            )
            .returning(WebhookEventModel.status)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_deliveries(
        self,
        statuses: Sequence[str],
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> Sequence[WebhookEventModel]:
        """Keyset page of deliveries in the given statuses, newest first."""
        stmt = select(WebhookEventModel).where(WebhookEventModel.status.in_(tuple(statuses)))
        if cursor is not None:
            cursor_at, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    WebhookEventModel.updated_at < cursor_at,
                    and_(
                        WebhookEventModel.updated_at == cursor_at,
                        WebhookEventModel.delivery_id < cursor_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            WebhookEventModel.updated_at.desc(), WebhookEventModel.delivery_id.desc()
        ).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()
