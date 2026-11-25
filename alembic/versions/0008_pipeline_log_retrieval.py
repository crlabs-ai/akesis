"""Durable workflow log retrieval state on pipelines.

Revision ID: 0008_pipeline_log_retrieval
Revises: 0007_worker_visibility

The retry worker claims pipelines whose verified incident still needs its workflow logs, with a
lease in log_next_attempt_at, and records the outcome. Existing rows start as pending; any that
lack verified provenance are rejected by the worker without a network request.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_pipeline_log_retrieval"
down_revision: str | None = "0007_worker_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column("log_status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.add_column(
        "pipelines", sa.Column("log_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("pipelines", sa.Column("log_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("pipelines", sa.Column("log_failure_code", sa.String(64)))
    op.create_index("ix_pipelines_log_due", "pipelines", ["log_status", "log_next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_pipelines_log_due", table_name="pipelines")
    op.drop_column("pipelines", "log_failure_code")
    op.drop_column("pipelines", "log_next_attempt_at")
    op.drop_column("pipelines", "log_attempts")
    op.drop_column("pipelines", "log_status")
