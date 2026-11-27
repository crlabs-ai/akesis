"""Owner attestation for Slack approver mappings (ADR-0012 §6).

Revision ID: 0011_slack_mapping_attestation
Revises: 0010_identity_workspaces
Create Date: 2026-11-27

Existing mappings get no attester: they are treated as unattributed and authorize no approval
until a workspace owner sets them again.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_slack_mapping_attestation"
down_revision: str | None = "0010_identity_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_memberships",
        sa.Column(
            "slack_mapped_by",
            sa.String(40),
            sa.ForeignKey(
                "users.user_id",
                ondelete="SET NULL",
                name="fk_workspace_memberships_slack_mapped_by",
            ),
        ),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("slack_mapped_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_workspace_memberships_slack_mapped_by", "workspace_memberships", type_="foreignkey"
    )
    op.drop_column("workspace_memberships", "slack_mapped_at")
    op.drop_column("workspace_memberships", "slack_mapped_by")
