"""create pipelines table

Revision ID: 0003_create_pipelines
Revises: 0002_create_mutations
Create Date: 2026-09-01 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_create_pipelines"
down_revision: str | None = "0002_create_mutations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("pipeline_id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("repository_owner", sa.String(length=128), nullable=False),
        sa.Column("repository_name", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("diagnosis_id", sa.String(length=64), nullable=True),
        sa.Column("proposal_id", sa.String(length=64), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column("mutation_id", sa.String(length=64), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("failure_context_json", sa.Text(), nullable=True),
        sa.Column("proposal_json", sa.Text(), nullable=True),
        sa.Column("validation_json", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("pipeline_id"),
        sa.UniqueConstraint("incident_id"),
    )
    op.create_index("ix_pipelines_incident_id", "pipelines", ["incident_id"])
    op.create_index("ix_pipelines_status", "pipelines", ["status"])
    op.create_index("ix_pipelines_proposal_id", "pipelines", ["proposal_id"])
    op.create_index("ix_pipelines_approval_id", "pipelines", ["approval_id"])
    op.create_index(
        "ix_pipelines_repo_run",
        "pipelines",
        ["repository_owner", "repository_name", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipelines_repo_run", table_name="pipelines")
    op.drop_index("ix_pipelines_approval_id", table_name="pipelines")
    op.drop_index("ix_pipelines_proposal_id", table_name="pipelines")
    op.drop_index("ix_pipelines_status", table_name="pipelines")
    op.drop_index("ix_pipelines_incident_id", table_name="pipelines")
    op.drop_table("pipelines")
