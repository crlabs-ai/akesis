"""Persist the next eligible webhook verification retry time."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_webhook_retry_schedule"
down_revision: str | None = "0005_create_webhook_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhook_events", sa.Column("next_retry_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("webhook_events", "next_retry_at")
