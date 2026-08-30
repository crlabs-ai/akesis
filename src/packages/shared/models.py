from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

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


class CodeEvidence(BaseModel):
    """Bounded, line-numbered source code snippet extracted from repository."""

    path: str = Field(..., description="Repository-relative file path")
    start_line: int = Field(..., description="1-indexed starting line number")
    end_line: int = Field(..., description="1-indexed ending line number")
    target_line: int | None = Field(default=None, description="Target line of interest if any")
    content: str = Field(..., description="Formatted source code lines with line numbers")
    total_file_lines: int = Field(..., description="Total line count of source file")
    language: str = Field(default="python", description="Programming language of source file")


class EvidencePackage(BaseModel):
    """Combined evidence package containing CI failure signal and verified repository code."""

    incident_id: str = Field(..., description="Associated Akesis incident identifier")
    commit_sha: str = Field(..., description="Exact checked out commit SHA")
    failure_context: FailureContext = Field(..., description="Normalized CI failure context")
    code_evidences: list[CodeEvidence] = Field(
        default_factory=list,
        description="Extracted repository source snippets relevant to failure",
    )
    retrieval_status: Literal["success", "partial", "unavailable", "empty"] = Field(
        default="unavailable",
        description="Status of codebase context resolution",
    )
    retrieval_notes: list[str] = Field(
        default_factory=list,
        description="Audit notes explaining file discovery and security decisions",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of evidence package creation",
    )


class EvidenceItem(BaseModel):
    """Specific verifiable piece of factual evidence extracted from logs or context."""

    source: str = Field(..., description="Source location (e.g. log_traceback, source_code)")
    observation: str = Field(..., description="Verifiable factual statement from the context")
    file_path: str | None = Field(
        default=None, description="Associated file if explicitly identified"
    )
    line_number: int | None = Field(
        default=None, description="Associated line number if explicitly identified"
    )


class RemediationDirection(BaseModel):
    """Recommended remediation guidance for human engineer review."""

    summary: str = Field(..., description="Concise overview of proposed fix approach")
    suggested_action: str = Field(..., description="Specific recommended change")
    risk_assessment: str = Field(
        ..., description="Potential side effects or regressions to watch for"
    )


class DiagnosisProposal(BaseModel):
    """Strict schema returned by LLM diagnostic baseline."""

    category: FailureCategory = Field(
        ...,
        description="Diagnosed category based on evidence",
    )
    root_cause: str = Field(
        ...,
        description="Clear, evidence-backed explanation of why the CI job failed",
    )
    evidence: list[EvidenceItem] = Field(
        ...,
        min_length=1,
        description="Explicit verifiable facts supporting this diagnosis",
    )
    target_file: str | None = Field(
        default=None,
        description="Primary source file requiring remediation if determinable",
    )
    target_line: int | None = Field(
        default=None,
        description="Primary line number requiring remediation if determinable",
    )
    remediation_direction: RemediationDirection = Field(
        ...,
        description="Proposed direction for resolving the failure",
    )
    is_fixable: bool = Field(
        ...,
        description="Whether this failure is deterministically remediable",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model-reported confidence score between 0.0 and 1.0",
    )
    evidence_sufficiency: Literal["sufficient", "partial", "insufficient"] = Field(
        ...,
        description="Assessment of whether supplied context was adequate for root cause diagnosis",
    )
    reasoning: str = Field(
        ...,
        description="Logical chain of reasoning connecting evidence to root cause",
    )


class DiagnosticResult(BaseModel):
    """Complete validated diagnostic result produced by DiagnosticService."""

    incident_id: str = Field(..., description="Associated Akesis incident identifier")
    proposal: DiagnosisProposal = Field(..., description="Validated diagnosis proposal")
    human_review_required: bool = Field(
        default=True,
        description="Always true in V1: human engineer must review and approve all proposals",
    )
    model_name: str = Field(..., description="Identifier of the model used for diagnosis")
    execution_time_ms: float = Field(
        ..., description="End-to-end diagnostic latency in milliseconds"
    )
    evidence_package: EvidencePackage | None = Field(
        default=None,
        description="Attached codebase evidence package if context resolution was active",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of diagnostic completion",
    )


class IngestionResponse(BaseModel):
    """HTTP response payload returned by the webhook ingestion gateway."""

    status: str = Field(..., description="Status string: accepted, ignored, or failed")
    incident_id: str | None = Field(default=None, description="Generated incident identifier")
    message: str = Field(..., description="Human-readable processing summary")
    category: str | None = Field(default=None, description="Extracted category if processed")
