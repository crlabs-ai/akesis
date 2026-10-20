from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.apps.api.main import app
from src.packages.database.repositories import ApprovalRepository, PipelineRepository
from src.packages.shared.config import settings
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureCategory,
    FailureContext,
    FailureSignal,
    FilePatch,
    FixProposal,
    PatchHunk,
    PipelineRecord,
    PipelineStatus,
    ValidationResult,
    ValidationStatus,
    WorkflowRunConclusion,
)

client = TestClient(app)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_incident_list_and_detail_routes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    unique_suffix = f"{now.timestamp():.6f}".replace(".", "_")
    incident_id = f"inc_api_test_{unique_suffix}"
    pipeline_id = f"pipe_api_test_{unique_suffix}"
    approval_id = f"appr_api_test_{unique_suffix}"
    proposal_id = f"prop_api_test_{unique_suffix}"

    failure_ctx = FailureContext(
        incident_id=incident_id,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=8899001122,
        workflow_name="CI Pipeline",
        commit_sha="fedcba654321",
        branch="feat/test-read-api",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/8899001122",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.LINT,
            error_type="RUFF_E999",
            message="SyntaxError: invalid syntax",
            target_file="src/models.py",
            target_line=15,
            extracted_snippet="SyntaxError: invalid syntax in src/models.py:15",
        ),
        raw_log_excerpt="SyntaxError: invalid syntax in src/models.py:15",
    )

    diff = "--- a/src/models.py\n+++ b/src/models.py\n@@ -15 +15 @@\n-x = \n+x = 1\n"
    file_patch = FilePatch(
        path="src/models.py",
        hunks=[
            PatchHunk(
                old_start=15,
                old_lines=1,
                new_start=15,
                new_lines=1,
                header="@@ -15 +15 @@",
                lines=["-x = ", "+x = 1"],
            )
        ],
        raw_diff=diff,
    )
    fix_prop = FixProposal(
        proposal_id=proposal_id,
        incident_id=incident_id,
        commit_sha="fedcba654321",
        status="proposed",
        is_valid=True,
        unified_diff=diff,
        file_patches=[file_patch],
        target_files=["src/models.py"],
        rationale="Fix syntax error",
        risk_level="low",
        confidence_score=0.95,
    )

    val_res = ValidationResult(
        validation_id=f"val_{unique_suffix}",
        proposal_id=proposal_id,
        incident_id=incident_id,
        commit_sha="fedcba654321",
        status=ValidationStatus.PASSED,
        command_executed="pytest",
        exit_code=0,
        stdout="1 passed in 0.05s",
        stderr="",
        duration_ms=150.0,
    )

    approval_rec = ApprovalRecord(
        approval_id=approval_id,
        incident_id=incident_id,
        proposal_id=proposal_id,
        commit_sha="fedcba654321",
        status=ApprovalStatus.PENDING,
        requested_at=now,
        expires_at=now + timedelta(hours=24),
        created_at=now,
        updated_at=now,
    )

    pipe_rec = PipelineRecord(
        pipeline_id=pipeline_id,
        incident_id=incident_id,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=8899001122,
        commit_sha="fedcba654321",
        status=PipelineStatus.AWAITING_APPROVAL,
        failure_context_json=failure_ctx.model_dump_json(),
        proposal_json=fix_prop.model_dump_json(),
        validation_json=val_res.model_dump_json(),
        approval_id=approval_id,
        pr_number=42,
        pr_url="https://github.com/crlabs-ai/akesis/pull/42",
        created_at=now,
        updated_at=now,
    )

    async with session_factory() as session:
        appr_repo = ApprovalRepository(session)
        await appr_repo.create_approval(approval_rec)
        pipe_repo = PipelineRepository(session)
        await pipe_repo.create_pipeline(pipe_rec)

    # 1. Test GET /v1/incidents (List View)
    response = client.get("/v1/incidents?limit=10&offset=0")
    assert response.status_code == 200
    incidents = response.json()
    assert isinstance(incidents, list)
    assert len(incidents) >= 1

    # Find created incident in list
    matching = [i for i in incidents if i["incident_id"] == incident_id]
    assert len(matching) == 1
    summary = matching[0]

    # Verify Summary Fields
    assert summary["incident_id"] == incident_id
    assert summary["repository"] == "crlabs-ai/akesis"
    assert summary["run_id"] == 8899001122
    assert summary["commit_sha"] == "fedcba654321"
    assert summary["workflow_name"] == "CI Pipeline"
    assert summary["branch"] == "feat/test-read-api"
    assert summary["category"] == "lint"
    assert summary["error_type"] == "RUFF_E999"
    assert summary["failure_message"] == "SyntaxError: invalid syntax"
    assert summary["current_stage"] == "awaiting_approval"
    assert summary["approval_status"] == "pending"
    assert summary["approval_id"] == approval_id
    assert summary["target_file"] == "src/models.py"
    assert summary["target_line"] == 15
    assert summary["pr_number"] == 42
    assert summary["pr_url"] == "https://github.com/crlabs-ai/akesis/pull/42"

    # CRITICAL: list does NOT contain diff_content
    assert "diff_content" not in summary

    # 2. Test GET /v1/incidents/{incident_id} (Detail View)
    detail_res = client.get(f"/v1/incidents/{incident_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()

    # Verify Detail Fields
    assert detail["incident_id"] == incident_id
    assert detail["repository"] == "crlabs-ai/akesis"
    assert detail["diff_content"] == diff
    assert detail["target_file"] == "src/models.py"
    assert detail["target_line"] == 15
    assert detail["sandbox_command"] == "pytest"
    assert detail["sandbox_exit_code"] == 0
    assert detail["sandbox_stdout"] == "1 passed in 0.05s"
    assert detail["sandbox_stderr"] == ""
    assert "SyntaxError" in detail["traceback_excerpt"]
    assert detail["failure_context"]["incident_id"] == incident_id
    assert detail["fix_proposal"]["proposal_id"] == proposal_id
    assert detail["validation_result"]["status"] == "passed"


def test_unknown_incident_404() -> None:
    res = client.get("/v1/incidents/inc_nonexistent_999999")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_malformed_json_handling(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    unique_suffix = f"{now.timestamp():.6f}".replace(".", "_")
    incident_id = f"inc_malformed_{unique_suffix}"
    pipeline_id = f"pipe_malformed_{unique_suffix}"

    pipe_rec = PipelineRecord(
        pipeline_id=pipeline_id,
        incident_id=incident_id,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=9999999999,
        commit_sha="corrupted123",
        status=PipelineStatus.FAILED,
        failure_context_json="{corrupted_json: true,",  # Invalid JSON
        proposal_json="invalid json diff",  # Invalid JSON
        validation_json="{invalid_val: ",  # Invalid JSON
        created_at=now,
        updated_at=now,
    )

    async with session_factory() as session:
        pipe_repo = PipelineRepository(session)
        await pipe_repo.create_pipeline(pipe_rec)

    # 1. Malformed list endpoint must NOT crash (safe deserialization)
    res_list = client.get("/v1/incidents?limit=50&offset=0")
    assert res_list.status_code == 200
    matching = [i for i in res_list.json() if i["incident_id"] == incident_id]
    assert len(matching) == 1
    assert matching[0]["incident_id"] == incident_id
    assert "diff_content" not in matching[0]

    # 2. Malformed detail endpoint must NOT crash (safe fallback)
    res_detail = client.get(f"/v1/incidents/{incident_id}")
    assert res_detail.status_code == 200
    data = res_detail.json()
    assert data["incident_id"] == incident_id
    assert data["diff_content"] is None
    assert data["failure_context"] is None
    assert data["fix_proposal"] is None
    assert data["validation_result"] is None


def test_pipeline_list_endpoint_and_pagination() -> None:
    # 1. Valid list
    res = client.get("/v1/pipelines?limit=10&offset=0")
    assert res.status_code == 200
    pipelines = res.json()
    assert isinstance(pipelines, list)

    if len(pipelines) > 0:
        first = pipelines[0]
        # Required summary fields present
        assert "pipeline_id" in first
        assert "incident_id" in first
        assert "repository_owner" in first
        assert "repository_name" in first
        assert "run_id" in first
        assert "commit_sha" in first
        assert "status" in first
        assert "created_at" in first
        assert "updated_at" in first

        # CRITICAL: Excluded internal JSON and diff fields across all returned records
        for p in pipelines:
            assert "failure_context_json" not in p
            assert "proposal_json" not in p
            assert "validation_json" not in p
            assert "unified_diff" not in p
            assert "diff_content" not in p

    # Deterministic ordering (created_at descending)
    if len(pipelines) >= 2:
        dates = [p["created_at"] for p in pipelines]
        assert dates == sorted(dates, reverse=True)

    # 2. Pagination parameter bounds validation
    # Limit too high (> 100)
    res_over = client.get("/v1/pipelines?limit=101")
    assert res_over.status_code == 422

    # Limit too low (< 1)
    res_under = client.get("/v1/pipelines?limit=0")
    assert res_under.status_code == 422

    # Negative offset (< 0)
    res_neg = client.get("/v1/pipelines?offset=-1")
    assert res_neg.status_code == 422

    # Incidents bounds validation
    assert client.get("/v1/incidents?limit=101").status_code == 422
    assert client.get("/v1/incidents?limit=0").status_code == 422
    assert client.get("/v1/incidents?offset=-5").status_code == 422
