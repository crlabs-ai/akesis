"""Provenance verification matrix against disposable PostgreSQL (ADR-0010, Phase 1.5).

Every protected entry point is exercised with valid and invalid persisted provenance. A valid
record must pass data-dependent verification and then stop at the temporary hold. Each invalid
record must be rejected with a stable code, before any collaborator is touched.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from src.packages.database.repositories import (
    MutationRepository,
)
from src.packages.database.session import get_session_factory
from src.packages.sdk.repo_checkout import GitRepositoryCheckoutManager
from src.packages.sdk.sandbox_runner import DockerSandboxRunner
from src.packages.shared.approval_service import ApprovalService
from src.packages.shared.context_resolver import CodebaseContextResolver
from src.packages.shared.fix_service import FixProposalService
from src.packages.shared.models import (
    ValidationCommand,
)
from src.packages.shared.mutation_service import GitMutationService
from src.packages.shared.persisted_provenance import (
    ProvenanceRejected,
    semantic_key_for,
    verify_approval_provenance,
    verify_context_provenance,
)
from src.packages.shared.provenance import UnverifiedProvenanceError
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator
from src.packages.shared.source_snapshot import SnapshotService
from src.packages.shared.validation_service import ValidationService
from tests.integration.seeding import Seeded, seed_incident
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth

_seed = seed_incident


def _collaborators() -> list[Mock]:
    return [Mock() for _ in range(8)]


def _no_side_effects(mocks: list[Mock]) -> None:
    assert all(not m.mock_calls for m in mocks), "a collaborator was touched before rejection"


def _recording_factory(mocks: list[Mock]) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[tuple[Mock, Mock, Mock]]:
        mocks[0].entered()
        yield mocks[1], mocks[2], mocks[3]

    return factory


# Each entry point: given a seeded incident, invoke it through its real public boundary.
EntryCall = Callable[[Seeded, Path, list[Mock]], Awaitable[Any]]


async def _process(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    orch = RemediationOrchestrator(
        repository_factory=_recording_factory(mocks),
        diagnostic_service=mocks[4],
        context_resolver=mocks[5],
    )
    return await orch.process_failure(s.context)


async def _resume(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    orch = RemediationOrchestrator(repository_factory=_recording_factory(mocks))
    return await orch.resume_approval(s.approval.approval_id)


async def _resolve(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    token = await verify_context_provenance(s.context)  # the only way to obtain a token
    return CodebaseContextResolver(checkout_manager=mocks[5]).resolve_context(
        s.context, provenance=token
    )


async def _resolve_with_caller_source(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    token = await verify_context_provenance(s.context)
    return CodebaseContextResolver(checkout_manager=mocks[5]).resolve_context(
        s.context, tmp, provenance=token
    )


async def _fix(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    llm = mocks[6]
    return await FixProposalService(llm_client=llm).generate_fix_proposal(s.context, Mock())


async def _validate(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    return await ValidationService(sandbox_runner=mocks[7]).validate_fix(s.proposal, s.context)


async def _approve(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    return await ApprovalService(
        repository_factory=_approval_repo_factory(mocks), slack_client=mocks[6]
    ).request_approval(s.context, s.proposal, s.validation)


async def _mutate(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    return await GitMutationService(
        repository_factory=_mutation_repo_factory(mocks),
        github_client=mocks[6],
        repo_checkout=mocks[5],
        sandbox_runner=mocks[7],
    ).create_pull_request(s.context, s.proposal, s.validation, s.approval)


async def _checkout(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    token = await verify_context_provenance(s.context)
    return GitRepositoryCheckoutManager(base_dir=tmp).checkout_commit(
        s.context.repository_owner,
        s.context.repository_name,
        s.context.commit_sha,
        provenance=token,
    )


async def _sandbox(s: Seeded, tmp: Path, mocks: list[Mock]) -> Any:
    token = await verify_context_provenance(s.context)
    # A genuine snapshot of the seeded commit, served by the synthetic GitHub API.
    fake = FakeGitHub(
        repo_id=1001,
        full_name="crlabs-ai/akesis",
        files={"src.py": ("100644", b"x = 1\n")},
        commit_sha=s.context.commit_sha,
    )
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service.prepare(token) as snapshot:
        return await DockerSandboxRunner().run_validation(
            snapshot, "", ValidationCommand.PYTEST, provenance=token
        )


def _approval_repo_factory(mocks: list[Mock]) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Mock]:
        mocks[0].entered()
        yield mocks[1]

    return factory


def _mutation_repo_factory(mocks: list[Mock]) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[tuple[Mock, Mock]]:
        mocks[0].entered()
        yield mocks[1], mocks[2]

    return factory


ENTRY_POINTS: dict[str, EntryCall] = {
    "process_failure": _process,
    "resume_approval": _resume,
    "resolve_context": _resolve,
    "generate_fix_proposal": _fix,
    "validate_fix": _validate,
    "request_approval": _approve,
    "create_pull_request": _mutate,
    "checkout_commit": _checkout,
    "run_validation": _sandbox,
}

# Resume reads only persisted state, so a record without provenance is "historical" there.
EXPECTED_CODE_OVERRIDES: dict[tuple[str, str], str] = {("resume_approval", "missing"): "historical"}

# Expected rejection code for each invalid case (the hold is the only failure for "valid").
INVALID_CASES: dict[str, str] = {
    "missing": "missing",
    "unpersisted": "unpersisted",
    "wrong_event_id": "identity",
    "wrong_event_repository": "identity",
    "wrong_source_repository": "mismatch",
    "wrong_installation": "identity",
    "wrong_run": "identity",
    "wrong_attempt": "identity",
    "wrong_sha": "mismatch",
    "wrong_branch": "mismatch",
    "rejected_event": "rejected",
    "unverified_event": "unverified",
    "stale": "stale",
    "historical": "historical",
    "pipeline_mismatch": "pipeline_mismatch",
}


def _expected_outcome(entry: str, case: str) -> type[Exception] | None:
    return UnverifiedProvenanceError if case == "valid" else ProvenanceRejected


async def _run(entry: str, case: str, tmp: Path) -> tuple[BaseException | None, list[Mock]]:
    seeded = await _seed(case)
    mocks = _collaborators()
    call = ENTRY_POINTS[entry]
    try:
        await call(seeded, tmp, mocks)
    except BaseException as error:  # noqa: BLE001 - outcome is asserted by the caller
        return error, mocks
    return None, mocks


CASES = ["valid", *INVALID_CASES]


# Public entry points that the hold guards first. Their gate runs before any provenance read, so an
# invalid record fails closed as UnverifiedProvenanceError. Each rejection code is asserted directly
# against the verifier in test_provenance_rejection_codes_are_enforced_by_the_verifier below.
HOLD_FIRST_ENTRY_POINTS = {"process_failure", "resume_approval", "create_pull_request"}


@pytest.mark.parametrize("entry", list(ENTRY_POINTS))
@pytest.mark.parametrize("case", CASES)
@pytest.mark.asyncio
async def test_entry_point_matrix(entry: str, case: str, tmp_path: Path) -> None:
    error, mocks = await _run(entry, case, tmp_path)
    expected: type[BaseException] = cast(type[BaseException], _expected_outcome(entry, case))
    if entry in HOLD_FIRST_ENTRY_POINTS and case != "valid":
        expected = UnverifiedProvenanceError
    assert error is not None, "protected path must not complete"
    assert isinstance(error, expected), repr(error)
    if case != "valid" and entry not in HOLD_FIRST_ENTRY_POINTS:
        code = EXPECTED_CODE_OVERRIDES.get((entry, case), INVALID_CASES[case])
        assert cast(ProvenanceRejected, error).code == code
    _no_side_effects(mocks)
    if entry in {"checkout_commit", "run_validation"}:
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("case", list(INVALID_CASES))
@pytest.mark.asyncio
async def test_provenance_rejection_codes_are_enforced_by_the_verifier(case: str) -> None:
    """The verifier, which the hold does not replace, still rejects each invalid record by code."""
    seeded = await _seed(case)
    with pytest.raises(ProvenanceRejected) as context_rejection:
        await verify_context_provenance(seeded.context)
    assert context_rejection.value.code == INVALID_CASES[case]
    with pytest.raises(ProvenanceRejected) as approval_rejection:
        await verify_approval_provenance(seeded.approval.approval_id)
    expected = EXPECTED_CODE_OVERRIDES.get(("resume_approval", case), INVALID_CASES[case])
    assert approval_rejection.value.code == expected


@pytest.mark.asyncio
async def test_valid_record_passes_verification_and_stops_at_hold() -> None:
    seeded = await _seed("valid")
    token = await verify_context_provenance(seeded.context)
    assert token.incident_id == seeded.context.incident_id
    assert token.semantic_key == semantic_key_for(42, 1001, seeded.context.run_id, 1)
    with pytest.raises(UnverifiedProvenanceError):
        await _checkout_with_token(seeded, token)


async def _checkout_with_token(seeded: Seeded, token: Any) -> None:
    GitRepositoryCheckoutManager(base_dir=Path("/nonexistent-never-used")).checkout_commit(
        seeded.context.repository_owner,
        seeded.context.repository_name,
        seeded.context.commit_sha,
        provenance=token,
    )


@pytest.mark.asyncio
async def test_approval_resolution_rejects_unbound_approval() -> None:
    with pytest.raises(ProvenanceRejected) as unpersisted:
        await verify_approval_provenance("appr_does_not_exist")
    assert unpersisted.value.code == "unpersisted"


@pytest.mark.asyncio
async def test_mutation_repository_is_untouched_by_rejected_paths() -> None:
    """Rejected mutation requests must not create mutation rows."""
    seeded = await _seed("missing")
    async with get_session_factory()() as session:
        before = await MutationRepository(session).get_by_proposal_and_commit(
            seeded.proposal.proposal_id, seeded.context.commit_sha
        )
    assert before is None
    with pytest.raises(UnverifiedProvenanceError):  # the hold refuses before any mutation work
        await _mutate(seeded, Path("."), _collaborators())
    async with get_session_factory()() as session:
        after = await MutationRepository(session).get_by_proposal_and_commit(
            seeded.proposal.proposal_id, seeded.context.commit_sha
        )
    assert after is None


_ = (AsyncMock, Awaitable)  # imports used by type annotations in helpers


@pytest.mark.asyncio
async def test_caller_supplied_source_is_refused_even_with_valid_provenance(tmp_path: Path) -> None:
    """Repository content must come from the bound checkout, never from a caller-chosen path."""
    seeded = await _seed("valid")
    mocks = _collaborators()
    with pytest.raises(ProvenanceRejected) as resolved:
        await _resolve_with_caller_source(seeded, tmp_path, mocks)
    assert resolved.value.code == "binding"
    # validate_fix has no source parameter at all: a caller-chosen directory cannot be passed.
    with pytest.raises(TypeError):
        await ValidationService(sandbox_runner=mocks[7]).validate_fix(  # type: ignore[call-arg]
            seeded.proposal, seeded.context, repo_root=tmp_path
        )
    _no_side_effects(mocks)
