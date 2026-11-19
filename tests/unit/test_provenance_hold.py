"""Protected entry-point tests that need no database (ADR-0010, Phase 1.5).

Every public remediation entry point must reject missing or forged provenance before any side
effect. Valid persisted provenance, the data-dependent acceptance path, is covered against real
PostgreSQL in tests/integration/test_provenance_matrix.py.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from src.packages.sdk.repo_checkout import GitRepositoryCheckoutManager
from src.packages.sdk.sandbox_runner import DockerSandboxRunner
from src.packages.shared.approval_service import ApprovalService
from src.packages.shared.context_resolver import CodebaseContextResolver
from src.packages.shared.fix_service import FixProposalService
from src.packages.shared.models import (
    FailureContext,
    FailureSignal,
    ValidationCommand,
    WorkflowRunConclusion,
)
from src.packages.shared.mutation_service import GitMutationService
from src.packages.shared.persisted_provenance import (
    ProvenanceRejected,
    VerifiedProvenance,
    require_bound_token,
)
from src.packages.shared.provenance import UnverifiedProvenanceError
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
from src.packages.shared.validation_service import ValidationService


def _context() -> FailureContext:
    return FailureContext(
        incident_id="inc_hold",
        repository_owner="owner",
        repository_name="repo",
        run_id=123,
        workflow_name="CI",
        commit_sha="a" * 40,
        branch="main",
        run_url="https://example.invalid/run/123",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(),
    )


def _counting_factory(pipe_repo: Mock) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[tuple[Mock, Mock, Mock]]:
        yield Mock(), Mock(), pipe_repo

    return factory


@pytest.mark.asyncio
async def test_async_entry_points_reject_missing_provenance_before_side_effects() -> None:
    llm = Mock()
    llm.generate_structured = AsyncMock()
    fix = FixProposalService(llm_client=llm)

    runner = Mock()
    runner.run_validation = AsyncMock()
    validation = ValidationService(sandbox_runner=runner)

    slack = Mock()
    slack.post_approval_card = AsyncMock()
    approval = ApprovalService(slack_client=slack)

    github = Mock()
    github.installation_token = AsyncMock()
    mutation = GitMutationService(auth=github)

    pipe_repo = Mock()
    pipe_repo.get_by_incident_id = AsyncMock(return_value=None)
    pipe_repo.create_pipeline = AsyncMock()
    diagnosis = Mock()
    diagnosis.diagnose_failure = AsyncMock()
    orchestrator = RemediationOrchestrator(
        repository_factory=_counting_factory(pipe_repo), diagnostic_service=diagnosis
    )

    ctx = cast(Any, _context())
    # The hold refuses before any database read or diagnosis, so no side effect can start.
    with pytest.raises(UnverifiedProvenanceError):
        await orchestrator.process_failure(ctx)
    pipe_repo.create_pipeline.assert_not_awaited()
    diagnosis.diagnose_failure.assert_not_awaited()

    with pytest.raises(ProvenanceRejected) as generate:
        await fix.generate_fix_proposal(ctx, cast(Any, None))
    assert generate.value.code == "missing"

    with pytest.raises(ProvenanceRejected) as validate:
        await validation.validate_fix(cast(Any, None), ctx)
    assert validate.value.code == "missing"

    with pytest.raises(ProvenanceRejected) as request:
        await approval.request_approval(ctx, cast(Any, None), cast(Any, None))
    assert request.value.code == "missing"

    with pytest.raises(UnverifiedProvenanceError):  # the hold refuses before any mutation work
        await mutation.create_pull_request(ctx, cast(Any, None), cast(Any, None), cast(Any, None))

    pipe_repo.get_by_incident_id.assert_not_awaited()
    pipe_repo.create_pipeline.assert_not_awaited()
    diagnosis.diagnose_failure.assert_not_awaited()
    llm.generate_structured.assert_not_awaited()
    runner.run_validation.assert_not_awaited()
    slack.post_approval_card.assert_not_awaited()
    github.installation_token.assert_not_awaited()  # no GitHub credential was issued


@pytest.mark.asyncio
async def test_resume_fails_closed_without_persisted_approval() -> None:
    """With no reachable database the resume entry point must reject, not proceed."""
    appr_repo = Mock()
    appr_repo.get_approval = AsyncMock()

    @asynccontextmanager
    async def factory() -> AsyncIterator[tuple[Mock, Mock, Mock]]:
        yield appr_repo, Mock(), Mock()

    # The hold refuses first, so the entry point fails closed before any database read.
    with pytest.raises(UnverifiedProvenanceError):
        await RemediationOrchestrator(repository_factory=factory).resume_approval(
            "historical-approval"
        )
    appr_repo.get_approval.assert_not_awaited()


def test_sync_entry_points_require_a_token_before_io(tmp_path: Path) -> None:
    checkout = Mock()
    with pytest.raises(ProvenanceRejected) as resolved:
        CodebaseContextResolver(checkout_manager=checkout).resolve_context(_context())
    assert resolved.value.code == "missing"
    checkout.checkout_commit.assert_not_called()

    with pytest.raises(ProvenanceRejected) as cloned:
        GitRepositoryCheckoutManager(base_dir=tmp_path).checkout_commit("owner", "repo", "a" * 40)
    assert cloned.value.code == "missing"
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_sandbox_requires_a_token_before_workspace_creation(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceRejected) as run:
        await DockerSandboxRunner().run_validation(tmp_path, "", ValidationCommand.PYTEST)
    assert run.value.code == "missing"
    assert not list(tmp_path.iterdir())


def test_tokens_cannot_be_forged() -> None:
    with pytest.raises(ProvenanceRejected) as forged:
        VerifiedProvenance(
            ticket="never-issued",
            incident_id="inc",
            pipeline_id="pipe_inc",
            delivery_id="d-1",
            owner="owner",
            name="repo",
            head_sha="a" * 40,
            head_branch="main",
            run_id=1,
            run_attempt=1,
            installation_id=1,
            event_repository_id=1,
            source_repository_id=1,
            verified_at=datetime.now(UTC),
            semantic_key="x",
        )
    assert forged.value.code == "forged"


def test_binding_rejects_absent_tokens() -> None:
    with pytest.raises(ProvenanceRejected) as absent:
        require_bound_token(None, owner="owner", name="repo", head_sha="a" * 40)
    assert absent.value.code == "missing"
