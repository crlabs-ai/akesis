from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy Declarative Base for Akesis database models."""

    pass


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

    __table_args__ = (Index("ix_approvals_status_expires", "status", "expires_at"),)


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
