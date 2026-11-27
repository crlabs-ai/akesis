"""Identity, sessions and workspaces; pipelines anchored to a verified installation (ADR-0012).

Revision ID: 0010_identity_workspaces
Revises: 0009_approval_execution

Adds users, server-side sessions, single-use OAuth sign-in attempts, workspaces (personal and
team), memberships with roles, invitations, linked GitHub App installations and their
repositories.

pipelines.installation_id is backfilled only from verified provenance stored in
failure_context_json. Rows whose JSON is malformed, or that carry no verified provenance, stay
NULL and are visible to no workspace. Nothing is guessed from unverified webhook claims.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0010_identity_workspaces"
down_revision: str | None = "0009_approval_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = "role IN ('owner', 'admin', 'maintainer', 'approver', 'viewer')"


def _ts(name: str, nullable: bool = True) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _stamp(name: str) -> sa.Column[datetime]:
    """A required timestamp the database fills in when the application does not."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(40), primary_key=True),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("github_login", sa.String(39), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("avatar_url", sa.String(512)),
        _stamp("created_at"),
        _stamp("updated_at"),
        _ts("last_login_at"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.String(40), primary_key=True),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_token_ciphertext", sa.Text()),
        _ts("github_token_expires_at"),
        _stamp("created_at"),
        _ts("expires_at", nullable=False),
        _ts("last_seen_at"),
        _ts("revoked_at"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "oauth_login_attempts",
        sa.Column("attempt_id", sa.String(40), primary_key=True),
        sa.Column("state_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_binding_sha256", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("return_to", sa.String(255), nullable=False),
        _stamp("created_at"),
        _ts("expires_at", nullable=False),
        _ts("consumed_at"),
    )
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "personal_owner_id",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            unique=True,
        ),
        sa.Column(
            "created_by",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.CheckConstraint("kind IN ('personal', 'team')", name="ck_workspaces_kind"),
        sa.CheckConstraint(
            "(kind = 'personal') = (personal_owner_id IS NOT NULL)",
            name="ck_workspaces_personal_owner",
        ),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("slack_user_id", sa.String(32)),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.CheckConstraint(_ROLES, name="ck_workspace_memberships_role"),
        sa.UniqueConstraint("workspace_id", "slack_user_id", name="uq_membership_slack_user"),
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    op.create_table(
        "workspace_invitations",
        sa.Column("invitation_id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Immutable target; the login is display-only and never used for matching.
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(39), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "invited_by",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        _stamp("created_at"),
        _ts("expires_at", nullable=False),
        _ts("accepted_at"),
        _ts("revoked_at"),
        sa.CheckConstraint(_ROLES, name="ck_workspace_invitations_role"),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_id", "workspace_invitations", ["workspace_id"]
    )
    op.create_index(
        "ix_workspace_invitations_github_user_id",
        "workspace_invitations",
        ["github_user_id"],
    )
    op.create_index(
        "uq_workspace_invitations_open",
        "workspace_invitations",
        ["workspace_id", "github_user_id"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_table(
        "github_installations",
        sa.Column("installation_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(39), nullable=False),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column(
            "linked_by",
            sa.String(40),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _stamp("linked_at"),
    )
    op.create_index(
        "ix_github_installations_workspace_id", "github_installations", ["workspace_id"]
    )
    op.create_table(
        "workspace_repositories",
        sa.Column("github_repository_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "installation_id",
            sa.BigInteger(),
            sa.ForeignKey("github_installations.installation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.true()),
        _stamp("synced_at"),
    )
    op.create_index(
        "ix_workspace_repositories_installation_id",
        "workspace_repositories",
        ["installation_id"],
    )

    op.add_column("pipelines", sa.Column("installation_id", sa.BigInteger()))
    op.create_index("ix_pipelines_installation_id", "pipelines", ["installation_id"])
    # Backfill from verified provenance only. pg_input_is_valid (PostgreSQL 16+) skips malformed
    # JSON instead of failing the migration; jsonb_typeof skips anything that is not a number.
    op.execute(
        """
    UPDATE pipelines AS p
    SET installation_id = (p.failure_context_json::jsonb #>> '{provenance,installation_id}')::bigint
    WHERE p.failure_context_json IS NOT NULL
      AND pg_input_is_valid(p.failure_context_json, 'jsonb')
      AND jsonb_typeof(p.failure_context_json::jsonb #> '{provenance,installation_id}') = 'number'
      AND p.failure_context_json::jsonb #>> '{provenance,verification_status}' = 'verified'
      AND (p.failure_context_json::jsonb #>> '{provenance,installation_id}') ~ '^[1-9][0-9]{0,18}$'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_pipelines_installation_id", table_name="pipelines")
    op.drop_column("pipelines", "installation_id")
    op.drop_table("workspace_repositories")
    op.drop_table("github_installations")
    op.drop_table("workspace_invitations")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("oauth_login_attempts")
    op.drop_table("auth_sessions")
    op.drop_table("users")
