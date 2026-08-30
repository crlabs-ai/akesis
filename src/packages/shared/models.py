from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FailureCategory(StrEnum):
    """Authoritative classification of failure categories in Akesis V1."""

    LINT = "lint"
    DEPENDENCY = "dependency"
    TEST = "test"
    BUILD = "build"
    UNKNOWN = "unknown"


class WorkflowRunConclusion(StrEnum):
    """GitHub Actions workflow run conclusions."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"
    NEUTRAL = "neutral"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class TracebackFrame(BaseModel):
    """Extracted stack trace frame information."""

    file_path: str = Field(..., description="Path to the source file in repository")
    line_number: int = Field(..., description="Line number of the invocation")
    function_name: str | None = Field(default=None, description="Enclosing function or method")
    code_line: str | None = Field(default=None, description="Code statement at line")


class FailureSignal(BaseModel):
    """Structured deterministic extraction of the primary failure in a CI log."""

    category: FailureCategory = Field(
        default=FailureCategory.UNKNOWN,
        description="Deterministic failure classification",
    )
    error_type: str = Field(
        default="UnknownError",
        description="Extracted error or exception identifier",
    )
    message: str = Field(
        default="",
        description="Extracted error summary or assertion message",
    )
    target_file: str | None = Field(
        default=None,
        description="Primary source file associated with failure",
    )
    target_line: int | None = Field(
        default=None,
        description="Primary line number associated with failure",
    )
    extracted_snippet: str = Field(
        default="",
        description="Filtered context window around the error signal",
    )
    traceback_frames: list[TracebackFrame] = Field(
        default_factory=list,
        description="Parsed stack trace frames if available",
    )


class WorkflowRunEvent(BaseModel):
    """Normalized representation of a GitHub workflow_run event."""

    event_type: str = Field(default="workflow_run", description="Event name")
    action: str = Field(..., description="Action trigger (e.g. completed)")
    repository_owner: str = Field(..., description="Owner or organization of repository")
    repository_name: str = Field(..., description="Name of repository")
    run_id: int = Field(..., description="GitHub workflow run identifier")
    workflow_name: str = Field(..., description="Display name of the workflow")
    head_branch: str = Field(..., description="Git branch of the workflow run")
    head_sha: str = Field(..., description="Commit SHA that triggered the run")
    run_url: str = Field(..., description="HTML URL to view the workflow run on GitHub")
    conclusion: WorkflowRunConclusion = Field(
        default=WorkflowRunConclusion.UNKNOWN,
        description="Final run conclusion",
    )
    sender: str | None = Field(default=None, description="Username of trigger initiator")


class FailureContext(BaseModel):
    """Complete structured failure context ready for downstream diagnostic ingestion."""

    incident_id: str = Field(..., description="Unique Akesis incident identifier")
    repository_owner: str = Field(..., description="Repository owner")
    repository_name: str = Field(..., description="Repository name")
    run_id: int = Field(..., description="Workflow run ID")
    workflow_name: str = Field(..., description="Workflow display name")
    commit_sha: str = Field(..., description="Commit SHA")
    branch: str = Field(..., description="Target git branch")
    run_url: str = Field(..., description="GitHub Actions run URL")
    conclusion: WorkflowRunConclusion = Field(..., description="Run conclusion")
    signal: FailureSignal = Field(..., description="Filtered and extracted error signal")
    raw_log_excerpt: str = Field(
        default="",
        description="Cleaned excerpt of the relevant failing log block",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of context creation",
    )


class IngestionResponse(BaseModel):
    """HTTP response payload returned by the webhook ingestion gateway."""

    status: str = Field(..., description="Status string: accepted, ignored, or failed")
    incident_id: str | None = Field(default=None, description="Generated incident identifier")
    message: str = Field(..., description="Human-readable processing summary")
    category: str | None = Field(default=None, description="Extracted category if processed")
