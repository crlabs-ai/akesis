"""Worker and operator visibility for webhook deliveries.

Revision ID: 0007_worker_visibility
Revises: 0006_webhook_retry_schedule

Adds the most recent transient error so exhaustion does not erase it, the authenticated event
context the worker needs to rebuild verification input (these are event claims, not verified
provenance), and indexes for due-delivery selection and keyset listing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_worker_visibility"
down_revision: str | None = "0006_webhook_retry_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhook_events", sa.Column("last_error_code", sa.String(64)))
    op.add_column("webhook_events", sa.Column("workflow_name", sa.String(255)))
    op.add_column("webhook_events", sa.Column("run_url", sa.String(512)))
    op.add_column("webhook_events", sa.Column("conclusion", sa.String(32)))
    op.create_index("ix_webhook_events_due", "webhook_events", ["status", "next_retry_at"])
    op.create_index("ix_webhook_events_listing", "webhook_events", ["updated_at", "delivery_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_events_listing", table_name="webhook_events")
    op.drop_index("ix_webhook_events_due", table_name="webhook_events")
    op.drop_column("webhook_events", "conclusion")
    op.drop_column("webhook_events", "run_url")
    op.drop_column("webhook_events", "workflow_name")
    op.drop_column("webhook_events", "last_error_code")
