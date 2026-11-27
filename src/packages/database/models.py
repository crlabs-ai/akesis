from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy Declarative Base for Akesis database models."""

    pass


class WebhookEventModel(Base):
    """Signed delivery metadata and its non-actionable verification lifecycle."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_due", "status", "next_retry_at"),
        Index("ix_webhook_events_listing", "updated_at", "delivery_id"),
    )

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    # Authenticated event claims used to rebuild verification input; not verified provenance.
    workflow_name: Mapped[str | None] = mapped_column(String(255))
    run_url: Mapped[str | None] = mapped_column(String(512))
    conclusion: Mapped[str | None] = mapped_column(String(32))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    event_repository_id: Mapped[int | None] = mapped_column(BigInteger)
    event_repository_name: Mapped[str | None] = mapped_column(String(255))
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    run_attempt: Mapped[int | None] = mapped_column(Integer)
    source_repository_id: Mapped[int | None] = mapped_column(BigInteger)
    source_repository_name: Mapped[str | None] = mapped_column(String(255))
    source_sha: Mapped[str | None] = mapped_column(String(40))
    source_branch: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    semantic_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    pipeline_id: Mapped[str | None] = mapped_column(ForeignKey("pipelines.pipeline_id"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalModel(Base):
    """Durable database model for human approval gate records."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        doc="Deterministic approval identifier",
    )
    incident_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="Associated incident identifier",
    )
    diagnosis_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Associated diagnosis identifier if available",
    )
    proposal_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        doc="Associated fix proposal identifier",
    )
    commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Target commit SHA",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        doc="State machine status: pending, approved, rejected, expired, cancelled",
    )
    slack_channel_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Slack channel identifier where card was posted",
    )
    slack_message_ts: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Slack message timestamp for updates",
    )
    reviewer: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="User ID or username who made decision",
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Reviewer explanation or rejection note",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        doc="Timestamp of record creation",
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        doc="Timestamp when approval was requested",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of reviewer decision",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when pending request expires",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        doc="Timestamp of last record update",
    )

    # Remediation execution (Phase 1.14c): pending -> running -> completed | failed | held.
    # Set to pending only by the same update that records an approval. While running,
    # execution_next_attempt_at is the claim lease; otherwise it is the next retry time.
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    execution_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_approvals_status_expires", "status", "expires_at"),
        Index("ix_approvals_execution_due", "execution_status", "execution_next_attempt_at"),
    )


class MutationModel(Base):
    """Durable database model for Git mutations and Pull Request creation records."""

    __tablename__ = "mutations"

    mutation_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        doc="Deterministic mutation identifier",
    )
    proposal_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="Associated fix proposal identifier",
    )
    approval_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="Associated approval identifier",
    )
    incident_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Associated CI incident identifier",
    )
    repository_owner: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc="Repository owner",
    )
    repository_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc="Repository name",
    )
    base_commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Target base commit SHA",
    )
    branch_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Dedicated fix branch name",
    )
    resulting_commit_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        doc="Resulting commit SHA produced on branch",
    )
    validation_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="Outcome of post-patch pre-push validation",
    )
    pr_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Sequential Pull Request number",
    )
    pr_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        doc="Web URL of created Pull Request",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        doc="Mutation lifecycle status",
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Explanation if mutation failed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        doc="Timestamp of mutation record creation",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        doc="Timestamp of last update",
    )

    __table_args__ = (Index("ix_mutations_proposal_commit", "proposal_id", "base_commit_sha"),)


class PipelineModel(Base):
    """Durable database model for end-to-end remediation pipeline orchestration state."""

    __tablename__ = "pipelines"

    pipeline_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        doc="Unique pipeline identifier",
    )
    incident_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        doc="Associated CI incident identifier",
    )
    repository_owner: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc="Repository owner",
    )
    repository_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc="Repository name",
    )
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
        doc="GitHub Actions workflow run identifier",
    )
    commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Target failing commit SHA",
    )
    # Phase 2 (ADR-0012): the GitHub App installation from the run's verified provenance. It is the
    # tenant anchor: a pipeline belongs to the workspace that has linked this installation. NULL
    # for historical rows without verified provenance, which no workspace can see.
    installation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        doc="Verified GitHub App installation that produced this pipeline",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="received",
        index=True,
        doc="Pipeline status e.g. received, diagnosing, awaiting_approval, completed, failed",
    )
    diagnosis_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Associated diagnosis identifier",
    )
    proposal_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc="Associated fix proposal identifier",
    )
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc="Associated approval identifier",
    )
    mutation_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Associated mutation identifier",
    )
    pr_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Created Pull Request number if completed",
    )
    pr_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        doc="Created Pull Request URL if completed",
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Summary explanation if pipeline stopped or failed",
    )
    failure_context_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Durable JSON serialization of FailureContext",
    )
    # Workflow log retrieval (Phase 1.10): pending -> fetching -> extracted | unavailable.
    # While fetching, log_next_attempt_at is the claim lease; otherwise it is the next retry time.
    log_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    log_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    log_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Durable JSON serialization of validated FixProposal",
    )
    validation_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Durable JSON serialization of ValidationResult",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        doc="Timestamp of pipeline initialization",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        doc="Timestamp of last pipeline update",
    )

    __table_args__ = (
        Index("ix_pipelines_repo_run", "repository_owner", "repository_name", "run_id"),
    )


# ---------------------------------------------------------------------------------------------
# Phase 2 (ADR-0012): identity, sessions and workspaces.
# ---------------------------------------------------------------------------------------------

WORKSPACE_KINDS = ("personal", "team")
# Ordered from most to least privileged; see src/packages/shared/workspace_roles.py.
WORKSPACE_ROLES = ("owner", "admin", "maintainer", "approver", "viewer")


def _now() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class UserModel(Base):
    """A person, identified by their immutable GitHub user ID (logins can change)."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    github_login: Mapped[str] = mapped_column(String(39), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSessionModel(Base):
    """A server-side browser session. Only a SHA-256 digest of the cookie token is stored."""

    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The GitHub user-to-server token, encrypted at rest, used only server-side to verify which
    # App installations this user can access. It dies with the session.
    github_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    github_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthLoginAttemptModel(Base):
    """One pending GitHub sign-in: its state, PKCE verifier and browser binding. Single use."""

    __tablename__ = "oauth_login_attempts"

    attempt_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    browser_binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    return_to: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceModel(Base):
    """A tenant. A personal workspace has exactly one owner, enforced by a unique column."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(_in("kind", WORKSPACE_KINDS), name="ck_workspaces_kind"),
        # personal <=> personal_owner_id is set; so each user has at most one personal workspace.
        CheckConstraint(
            "(kind = 'personal') = (personal_owner_id IS NOT NULL)",
            name="ck_workspaces_personal_owner",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    personal_owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), unique=True
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class WorkspaceMembershipModel(Base):
    """A user's role in a workspace. Deleting the row revokes access immediately."""

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint(_in("role", WORKSPACE_ROLES), name="ck_workspace_memberships_role"),
        # A Slack identity maps to at most one member per workspace (set by owners only).
        UniqueConstraint("workspace_id", "slack_user_id", name="uq_membership_slack_user"),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    slack_user_id: Mapped[str | None] = mapped_column(String(32))
    # Owner attestation (ADR-0012 §6): who mapped the Slack identity, and when. A mapping without
    # both, or whose attester is no longer an owner of this workspace, authorizes nothing. This is
    # an attestation by a person, not cryptographic proof that the member controls the Slack ID.
    slack_mapped_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    slack_mapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class WorkspaceInvitationModel(Base):
    """An invitation for one GitHub account to join a team workspace with a role.

    The target is the immutable numeric GitHub user ID, resolved from the username when the
    invitation is created. `github_login` is display-only: a rename never moves an invitation."""

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint(_in("role", WORKSPACE_ROLES), name="ck_workspace_invitations_role"),
        # At most one open invitation per GitHub account per workspace.
        Index(
            "uq_workspace_invitations_open",
            "workspace_id",
            "github_user_id",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    invitation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    github_login: Mapped[str] = mapped_column(String(39), nullable=False)  # display only
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_by: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitHubInstallationModel(Base):
    """A GitHub App installation linked to exactly one workspace (the primary key enforces it)."""

    __tablename__ = "github_installations"

    installation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(39), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    linked_by: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkspaceRepositoryModel(Base):
    """A repository reachable through a linked installation, and so owned by its workspace."""

    __tablename__ = "workspace_repositories"

    github_repository_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("github_installations.installation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
