from datetime import UTC, datetime

from src.packages.shared.models import (
    FailureContext,
    FixProposal,
    IncidentDetail,
    IncidentSummary,
    ValidationResult,
)


def test_incident_summary_model_fields() -> None:
    now = datetime.now(UTC)
    summary = IncidentSummary(
        incident_id="inc_test_123",
        repository="crlabs-ai/akesis",
        run_id=123456789,
        commit_sha="abcdef123456",
        workflow_name="CI",
        branch="main",
        category="lint",
        error_type="RUFF_ERROR",
        failure_message="Undefined variable",
        current_stage="RECEIVED",
        approval_status="pending",
        approval_id="appr_123",
        target_file="src/app.py",
        target_line=42,
        pr_number=10,
        pr_url="https://github.com/crlabs-ai/akesis/pull/10",
        created_at=now,
        updated_at=now,
    )

    data = summary.model_dump()
    assert "incident_id" in data
    assert "repository" in data
    assert "diff_content" not in data
    assert data["incident_id"] == "inc_test_123"
    assert data["target_file"] == "src/app.py"
    assert data["target_line"] == 42


def test_incident_detail_model_fields() -> None:
    now = datetime.now(UTC)
    detail = IncidentDetail(
        incident_id="inc_test_123",
        repository="crlabs-ai/akesis",
        run_id=123456789,
        commit_sha="abcdef123456",
        workflow_name="CI",
        branch="main",
        category="lint",
        error_type="RUFF_ERROR",
        failure_message="Undefined variable",
        current_stage="AWAITING_APPROVAL",
        approval_status="pending",
        approval_id="appr_123",
        target_file="src/app.py",
        target_line=42,
        pr_number=10,
        pr_url="https://github.com/crlabs-ai/akesis/pull/10",
        created_at=now,
        updated_at=now,
        diff_content="--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-import os\n+import sys\n",
        failure_context=FailureContext.model_validate({
            "incident_id": "inc_test_123",
            "repository_owner": "crlabs-ai",
            "repository_name": "akesis",
            "run_id": 123456789,
            "workflow_name": "CI",
            "commit_sha": "abcdef123456",
            "branch": "main",
            "run_url": "",
            "conclusion": "failure",
            "signal": {
                "category": "lint",
                "error_type": "RUFF_ERROR",
                "failure_message": "Undefined variable",
                "extracted_snippet": "F821 Undefined name",
            },
            "raw_log_excerpt": "F821 Undefined name",
        }),
        fix_proposal=FixProposal.model_validate({
            "proposal_id": "prop_123",
            "incident_id": "inc_test_123",
            "commit_sha": "abcdef123456",
            "status": "proposed",
            "is_valid": True,
            "rationale": "Remove unused import",
            "risk_level": "low",
            "confidence_score": 0.95,
            "unified_diff": (
                "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-import os\n+import sys\n"
            ),
        }),
        validation_result=ValidationResult.model_validate({
            "validation_id": "val_123",
            "proposal_id": "prop_123",
            "incident_id": "inc_test_123",
            "commit_sha": "abcdef123456",
            "status": "passed",
            "command_executed": "pytest",
            "exit_code": 0,
            "stdout": "1 passed",
            "stderr": "",
            "duration_ms": 120.0,
        }),
        traceback_excerpt="F821 Undefined name",
        sandbox_command="pytest",
        sandbox_exit_code=0,
        sandbox_stdout="1 passed",
        sandbox_stderr="",
    )

    data = detail.model_dump()
    assert "diff_content" in data
    assert data["diff_content"].startswith("--- a/src/app.py")
    assert data["sandbox_command"] == "pytest"
    assert data["sandbox_exit_code"] == 0
    assert data["sandbox_stdout"] == "1 passed"


def test_pipeline_summary_model_fields() -> None:
    from src.packages.shared.models import PipelineStatus, PipelineSummary

    now = datetime.now(UTC)
    summary = PipelineSummary(
        pipeline_id="pipe_123",
        incident_id="inc_123",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=98765432101,
        commit_sha="abcdef123456",
        status=PipelineStatus.AWAITING_APPROVAL,
        diagnosis_id="diag_123",
        proposal_id="prop_123",
        approval_id="appr_123",
        mutation_id="mut_123",
        pr_number=50,
        pr_url="https://github.com/crlabs-ai/akesis/pull/50",
        failure_reason="Syntax error",
        created_at=now,
        updated_at=now,
    )

    data = summary.model_dump()
    assert data["pipeline_id"] == "pipe_123"
    assert data["incident_id"] == "inc_123"
    assert data["run_id"] == 98765432101
    assert data["status"] == "awaiting_approval"

    # Verify absence of internal JSON blobs and diffs
    assert "failure_context_json" not in data
    assert "proposal_json" not in data
    assert "validation_json" not in data
    assert "unified_diff" not in data
    assert "diff_content" not in data
