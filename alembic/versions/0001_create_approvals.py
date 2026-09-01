"""create approvals table

Revision ID: 0001_create_approvals
Revises:
Create Date: 2026-08-31 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_create_approvals"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=64), nullable=True),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("slack_channel_id", sa.String(length=64), nullable=True),
        sa.Column("slack_message_ts", sa.String(length=64), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint("proposal_id"),
    )
    op.create_index("ix_approvals_incident_id", "approvals", ["incident_id"])
    op.create_index("ix_approvals_proposal_id", "approvals", ["proposal_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_status_expires", "approvals", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_approvals_status_expires", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_proposal_id", table_name="approvals")
    op.drop_index("ix_approvals_incident_id", table_name="approvals")
    op.drop_table("approvals")
