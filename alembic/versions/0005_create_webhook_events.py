"""Create durable webhook delivery records.

Revision ID: 0005_create_webhook_events
Revises: 0004_alter_run_id_bigint
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_create_webhook_events"
down_revision: str | None = "0004_alter_run_id_bigint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("delivery_id", sa.String(128), primary_key=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64)),
        sa.Column("installation_id", sa.BigInteger()),
        sa.Column("event_repository_id", sa.BigInteger()),
        sa.Column("event_repository_name", sa.String(255)),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column("run_attempt", sa.Integer()),
        sa.Column("source_repository_id", sa.BigInteger()),
        sa.Column("source_repository_name", sa.String(255)),
        sa.Column("source_sha", sa.String(40)),
        sa.Column("source_branch", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("semantic_key", sa.String(64), unique=True),
        sa.Column("pipeline_id", sa.String(64), sa.ForeignKey("pipelines.pipeline_id")),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
