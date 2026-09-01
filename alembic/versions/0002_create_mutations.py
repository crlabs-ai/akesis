"""create mutations table

Revision ID: 0002_create_mutations
Revises: 0001_create_approvals
Create Date: 2026-09-01 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_create_mutations"
down_revision: str | None = "0001_create_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mutations",
        sa.Column("mutation_id", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("repository_owner", sa.String(length=128), nullable=False),
        sa.Column("repository_name", sa.String(length=128), nullable=False),
        sa.Column("base_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("branch_name", sa.String(length=255), nullable=False),
        sa.Column("resulting_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("validation_status", sa.String(length=32), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("mutation_id"),
    )
    op.create_index("ix_mutations_proposal_id", "mutations", ["proposal_id"])
    op.create_index("ix_mutations_approval_id", "mutations", ["approval_id"])
    op.create_index("ix_mutations_status", "mutations", ["status"])
    op.create_index(
        "ix_mutations_proposal_commit",
        "mutations",
        ["proposal_id", "base_commit_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_mutations_proposal_commit", table_name="mutations")
    op.drop_index("ix_mutations_status", table_name="mutations")
    op.drop_index("ix_mutations_approval_id", table_name="mutations")
    op.drop_index("ix_mutations_proposal_id", table_name="mutations")
    op.drop_table("mutations")
