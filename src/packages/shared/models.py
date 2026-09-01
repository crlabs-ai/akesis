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


class PatchHunk(BaseModel):
    """Parsed single hunk of a unified diff."""

    old_start: int = Field(..., description="Starting line in original file")
    old_lines: int = Field(..., description="Number of lines in original file hunk")
    new_start: int = Field(..., description="Starting line in modified file")
    new_lines: int = Field(..., description="Number of lines in modified file hunk")
    header: str = Field(..., description="Hunk header string e.g. @@ -1,5 +1,6 @@")
    lines: list[str] = Field(..., description="Individual hunk lines with +/-/ prefix")


class FilePatch(BaseModel):
    """Structured patch representation for an individual file."""

    path: str = Field(..., description="Repository-relative target file path")
    old_path: str | None = Field(default=None, description="Source path in diff header")
    new_path: str | None = Field(default=None, description="Target path in diff header")
    hunks: list[PatchHunk] = Field(default_factory=list, description="Parsed diff hunks")
    raw_diff: str = Field(..., description="Unified diff text for this specific file")


class RawFixProposal(BaseModel):
    """Strict schema requested from LLM for fix generation."""

    explanation: str = Field(..., description="Technical rationale for the proposed fix")
    target_files: list[str] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="List of repository-relative file paths modified by the patch",
    )
    unified_diff: str = Field(..., description="Standard unified diff format patch")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Key assumptions made by the model when constructing the fix",
    )
    risk_assessment: str = Field(
        ..., description="Assessment of potential regression risks or side effects"
    )
    estimated_risk_level: Literal["low", "medium", "high"] = Field(
        ..., description="Estimated risk tier of the proposed change"
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model confidence in the correctness of this proposed fix",
    )


class FixProposal(BaseModel):
    """Authoritative, validated fix proposal ready for sandbox validation."""

    proposal_id: str = Field(..., description="Deterministic proposal identifier")
    incident_id: str = Field(..., description="Associated Akesis incident identifier")
    diagnosis_id: str | None = Field(
        default=None, description="Associated diagnosis identifier if available"
    )
    commit_sha: str = Field(..., description="Target commit SHA")
    status: Literal["proposed", "rejected", "ineligible"] = Field(
        ..., description="Validation and eligibility status of the proposal"
    )
    is_valid: bool = Field(..., description="True if patch passes all deterministic safety checks")
    rejection_reasons: list[str] = Field(
        default_factory=list,
        description="List of reasons if the proposal was rejected or deemed ineligible",
    )
    unified_diff: str = Field(default="", description="Validated canonical unified diff patch")
    file_patches: list[FilePatch] = Field(
        default_factory=list, description="Parsed structured per-file patches"
    )
    target_files: list[str] = Field(
        default_factory=list, description="List of validated target file paths"
    )
    rationale: str = Field(..., description="Human-readable technical rationale")
    assumptions: list[str] = Field(
        default_factory=list, description="Key assumptions underlying the fix"
    )
    risk_level: Literal["low", "medium", "high"] = Field(..., description="Evaluated risk tier")
    has_dependency_changes: bool = Field(
        default=False, description="True if the patch touches dependencies or package configuration"
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Bounded confidence score between 0.0 and 1.0",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of proposal generation",
    )


class ValidationCommand(StrEnum):
    """Allowlisted deterministic commands supported in sandbox validation."""

    PYTEST = "pytest"
    RUFF = "ruff"
    MYPY = "mypy"
    PYTHON_SYNTAX = "python_syntax"


class ValidationStatus(StrEnum):
    """Outcome status of sandbox validation."""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PATCH_REJECTED = "patch_rejected"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class ValidationResult(BaseModel):
    """Authoritative structured result of sandbox fix validation."""

    validation_id: str = Field(..., description="Deterministic validation identifier")
    proposal_id: str = Field(..., description="Associated FixProposal identifier")
    incident_id: str = Field(..., description="Associated incident identifier")
    commit_sha: str = Field(..., description="Target commit SHA validated")
    status: ValidationStatus = Field(..., description="Outcome status of validation")
    command_executed: str = Field(..., description="Allowlisted command identifier")
    exit_code: int | None = Field(default=None, description="Process exit code")
    stdout: str = Field(default="", description="Bounded stdout output")
    stderr: str = Field(default="", description="Bounded stderr output")
    duration_ms: float = Field(..., description="Total validation execution latency")
    timed_out: bool = Field(default=False, description="True if execution exceeded timeout")
    failure_reason: str | None = Field(
        default=None, description="Summary explanation of failure if any"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of validation completion",
    )


class ApprovalStatus(StrEnum):
    """Authoritative state machine states for human review."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalRecord(BaseModel):
    """Authoritative domain representation of an approval gate entity."""

    approval_id: str = Field(..., description="Deterministic approval identifier")
    incident_id: str = Field(..., description="Associated incident identifier")
    diagnosis_id: str | None = Field(default=None, description="Associated diagnosis ID")
    proposal_id: str = Field(..., description="Associated FixProposal identifier")
    commit_sha: str = Field(..., description="Target commit SHA")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, description="Approval state")
    slack_channel_id: str | None = Field(default=None, description="Posted Slack channel")
    slack_message_ts: str | None = Field(default=None, description="Posted Slack message timestamp")
    requested_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when human review was requested",
    )
    decided_at: datetime | None = Field(default=None, description="Timestamp of human decision")
    decided_by: str | None = Field(default=None, description="Reviewer identity")
    decision_reason: str | None = Field(default=None, description="Reviewer explanation or notes")
    expires_at: datetime | None = Field(default=None, description="Timestamp of expiration")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Record creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Record last updated timestamp",
    )


class MutationStatus(StrEnum):
    """State machine states for Git mutation and Pull Request lifecycle."""

    PENDING = "pending"
    APPLYING = "applying"
    VALIDATED = "validated"
    COMMITTED = "committed"
    PUSHED = "pushed"
    PR_CREATED = "pr_created"
    FAILED = "failed"


class PRMetadata(BaseModel):
    """Structured metadata returned from GitHub Pull Request creation."""

    pr_number: int = Field(..., description="GitHub PR sequential identifier")
    pr_url: str = Field(..., description="REST API URL for PR resource")
    html_url: str = Field(..., description="Web URL for viewing PR in browser")
    title: str = Field(..., description="PR display title")
    head_branch: str = Field(..., description="Source branch for PR")
    base_branch: str = Field(..., description="Target base branch for PR")


class MutationRecord(BaseModel):
    """Authoritative representation of a Git mutation and PR creation lifecycle entity."""

    mutation_id: str = Field(..., description="Deterministic mutation identifier")
    proposal_id: str = Field(..., description="Associated FixProposal identifier")
    approval_id: str = Field(..., description="Associated ApprovalRecord identifier")
    incident_id: str = Field(..., description="Associated CI incident identifier")
    repository_owner: str = Field(..., description="Owner of the target repository")
    repository_name: str = Field(..., description="Name of the target repository")
    base_commit_sha: str = Field(..., description="Exact base commit SHA mutated")
    branch_name: str = Field(..., description="Deterministic fix branch created")
    resulting_commit_sha: str | None = Field(
        default=None, description="Resulting commit SHA produced by the mutation"
    )
    validation_status: ValidationStatus | None = Field(
        default=None, description="Pre-push sandbox validation outcome"
    )
    pr_number: int | None = Field(default=None, description="Sequential number of created PR")
    pr_url: str | None = Field(default=None, description="Web URL of created Pull Request")
    status: MutationStatus = Field(
        default=MutationStatus.PENDING, description="Current mutation status"
    )
    failure_reason: str | None = Field(
        default=None, description="Detailed explanation if mutation failed"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of mutation initialization",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of mutation last update",
    )


class PipelineStatus(StrEnum):
    """Authoritative lifecycle state machine states for end-to-end remediation pipeline."""

    RECEIVED = "received"
    DIAGNOSING = "diagnosing"
    PROPOSING = "proposing"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    MUTATING = "mutating"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineRecord(BaseModel):
    """Authoritative representation of an end-to-end remediation orchestration pipeline entity."""

    pipeline_id: str = Field(..., description="Unique pipeline execution identifier")
    incident_id: str = Field(..., description="Associated CI incident identifier")
    repository_owner: str = Field(..., description="Repository owner")
    repository_name: str = Field(..., description="Repository name")
    run_id: int = Field(..., description="GitHub workflow run identifier")
    commit_sha: str = Field(..., description="Target commit SHA")
    status: PipelineStatus = Field(
        default=PipelineStatus.RECEIVED,
        description="Current orchestration pipeline status",
    )
    diagnosis_id: str | None = Field(default=None, description="Associated diagnosis identifier")
    proposal_id: str | None = Field(default=None, description="Associated fix proposal identifier")
    approval_id: str | None = Field(
        default=None, description="Associated approval record identifier"
    )
    mutation_id: str | None = Field(
        default=None, description="Associated mutation record identifier"
    )
    pr_number: int | None = Field(
        default=None, description="Created Pull Request number if completed"
    )
    pr_url: str | None = Field(default=None, description="Created Pull Request URL if completed")
    failure_reason: str | None = Field(
        default=None, description="Summary explanation if pipeline stopped or failed"
    )
    failure_context_json: str | None = Field(
        default=None, description="Durable JSON serialization of FailureContext"
    )
    proposal_json: str | None = Field(
        default=None, description="Durable JSON serialization of validated FixProposal"
    )
    validation_json: str | None = Field(
        default=None, description="Durable JSON serialization of ValidationResult"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Pipeline initiation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Pipeline last update timestamp",
    )


class IngestionResponse(BaseModel):
    """HTTP response payload returned by the webhook ingestion gateway."""

    status: str = Field(..., description="Status string: accepted, ignored, or failed")
    incident_id: str | None = Field(default=None, description="Generated incident identifier")
    message: str = Field(..., description="Human-readable processing summary")
    category: str | None = Field(default=None, description="Extracted category if processed")
