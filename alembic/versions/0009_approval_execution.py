"""Durable remediation execution state on approvals (Phase 1.14c).

Revision ID: 0009_approval_execution
Revises: 0008_pipeline_log_retrieval

Recording an approval sets execution_status = pending in the same conditional update, so a
repeated Slack delivery can never create a second job. The remediation worker claims pending rows
with a lease in execution_next_attempt_at. Existing approvals keep a NULL execution_status: they
were never queued, and are only queued by an operator.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_approval_execution"
down_revision: str | None = "0008_pipeline_log_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("execution_status", sa.String(32)))
    op.add_column(
        "approvals",
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("approvals", sa.Column("execution_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("approvals", sa.Column("execution_failure_code", sa.String(64)))
    op.create_index(
        "ix_approvals_execution_due", "approvals", ["execution_status", "execution_next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_execution_due", table_name="approvals")
    op.drop_column("approvals", "execution_failure_code")
    op.drop_column("approvals", "execution_next_attempt_at")
    op.drop_column("approvals", "execution_attempts")
    op.drop_column("approvals", "execution_status")
