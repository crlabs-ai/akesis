"""Controlled historical recovery against disposable PostgreSQL (ADR-0010, Phase 1.5).

The recovery service is the only path, besides signed ingestion, that adds provenance to a
historical pipeline. The GitHub verifier is a controlled stub passed in as an argument. Its real
installation-scoped behaviour is covered by tests/unit/test_workflow_provenance.py.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import update

from src.packages.database.models import PipelineModel, WebhookEventModel
from src.packages.database.repositories import PipelineRepository
from src.packages.database.session import get_session_factory
from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    PipelineRecord,
    PipelineStatus,
    RepositoryIdentity,
    WorkflowRunConclusion,
    WorkflowRunProvenance,
)
from src.packages.shared.persisted_provenance import (
    ProvenanceRejected,
    semantic_key_for,
    verify_context_provenance,
)
from src.packages.shared.provenance_recovery import recover_historical_provenance
from src.packages.shared.workflow_provenance import ProvenanceVerificationError


class StubVerifier:
    """Controlled stand-in for the installation-scoped GitHub verifier."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []
        self.error = error

    async def verify(self, payload: dict[str, Any], delivery_id: str) -> WorkflowRunProvenance:
        self.calls.append((payload, delivery_id))
        if self.error is not None:
            raise self.error
        run = payload["workflow_run"]
        repo = payload["repository"]
        source = run["head_repository"]
        return WorkflowRunProvenance(
            delivery_id=delivery_id,
            event_repository=RepositoryIdentity(id=repo["id"], full_name=repo["full_name"]),
            source_repository=RepositoryIdentity(id=source["id"], full_name=source["full_name"]),
            head_sha=run["head_sha"],
            head_branch=run["head_branch"],
            run_id=run["id"],
            run_attempt=run["run_attempt"],
            installation_id=payload["installation"]["id"],
            verified_at=datetime.now(UTC),
        )


def _sha() -> str:
    return hashlib.sha1(uuid.uuid4().bytes).hexdigest()  # noqa: S324 - random test identifier


async def _seed_historical(
    *, bound_events: int = 1, drop_repo_id: bool = False, semantic_bad: bool = False
) -> tuple[str, str, FailureContext]:
    """Persist a pre-provenance pipeline and its verified webhook event(s)."""
    now = datetime.now(UTC)
    uid = uuid.uuid4().hex[:12]
    incident_id = f"inc_hist_{uid}"
    pipeline_id = f"pipe_{incident_id}"
    run_id = int(uuid.uuid4().int % (2**40)) + 1
    sha = _sha()
    context = FailureContext(
        incident_id=incident_id,
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=run_id,
        workflow_name="CI",
        commit_sha=sha,
        branch="main",
        run_url="https://example.invalid/run",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(category=FailureCategory.TEST),
    )
    async with get_session_factory()() as session:
        await PipelineRepository(session).create_pipeline(
            PipelineRecord(
                pipeline_id=pipeline_id,
                incident_id=incident_id,
                repository_owner="crlabs-ai",
                repository_name="akesis",
                run_id=run_id,
                commit_sha=sha,
                status=PipelineStatus.FAILED,
                failure_reason="legacy record",
                failure_context_json=context.model_dump_json(),
                created_at=now,
                updated_at=now,
            )
        )
        for index in range(bound_events):
            delivery_id = f"hist-{uid}-{index}"
            semantic = semantic_key_for(42, 1001, run_id, 1)
            if semantic_bad:
                semantic = semantic_key_for(42, 1001, run_id + 1, 1)
            session.add(
                WebhookEventModel(
                    delivery_id=delivery_id,
                    payload_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    event_type="workflow_run",
                    action="completed",
                    installation_id=42,
                    event_repository_id=None if drop_repo_id else 1001,
                    event_repository_name="crlabs-ai/akesis",
                    run_id=run_id,
                    run_attempt=1,
                    source_repository_id=1001,
                    source_repository_name="crlabs-ai/akesis",
                    source_sha=sha,
                    source_branch="main",
                    status="verified",
                    semantic_key=semantic
                    if index == 0
                    else hashlib.sha256(f"{semantic}-{index}".encode()).hexdigest(),
                    pipeline_id=pipeline_id,
                    attempt_count=1,
                    received_at=now,
                    updated_at=now,
                    verified_at=now - timedelta(hours=1),
                    processed_at=now,
                )
            )
        await session.commit()
    return incident_id, pipeline_id, context


async def _run_recovery(pipeline_id: str, verifier: StubVerifier) -> bool:
    async with get_session_factory()() as session:
        recovered = await recover_historical_provenance(session, pipeline_id, cast(Any, verifier))
        await session.commit()
    return recovered


async def _stored_context(pipeline_id: str) -> FailureContext:
    async with get_session_factory()() as session:
        pipeline = await session.get(PipelineModel, pipeline_id)
    assert pipeline is not None and pipeline.failure_context_json is not None
    return FailureContext.model_validate_json(pipeline.failure_context_json)


@pytest.mark.asyncio
async def test_eligible_record_recovers_into_persisted_verified_provenance() -> None:
    _, pipeline_id, _ = await _seed_historical()
    verifier = StubVerifier()

    assert await _run_recovery(pipeline_id, verifier) is True
    assert len(verifier.calls) == 1
    payload, delivery_id = verifier.calls[0]
    assert delivery_id.startswith("hist-")
    assert payload["installation"]["id"] == 42

    recovered = await _stored_context(pipeline_id)
    assert recovered.provenance is not None
    assert recovered.provenance.delivery_id == delivery_id

    # The recovered record now satisfies the same persisted verifier as ingestion does.
    token = await verify_context_provenance(recovered)
    assert token.pipeline_id == pipeline_id
    assert token.delivery_id == delivery_id


@pytest.mark.asyncio
async def test_already_bound_record_is_not_reverified() -> None:
    _, pipeline_id, _ = await _seed_historical()
    assert await _run_recovery(pipeline_id, StubVerifier()) is True
    verifier = StubVerifier()
    assert await _run_recovery(pipeline_id, verifier) is True
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_unrecoverable_record_stays_non_actionable() -> None:
    _, pipeline_id, _ = await _seed_historical()
    # Remove the only event binding: nothing can be recovered without inference.
    async with get_session_factory()() as session:
        await session.execute(
            update(WebhookEventModel)
            .where(WebhookEventModel.pipeline_id == pipeline_id)
            .values(status="received")
        )
        await session.commit()

    verifier = StubVerifier()
    assert await _run_recovery(pipeline_id, verifier) is False
    assert verifier.calls == []
    stored = await _stored_context(pipeline_id)
    assert stored.provenance is None
    with pytest.raises(ProvenanceRejected) as historical:
        await verify_context_provenance(stored.model_copy(update={"provenance": None}))
    assert historical.value.code == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({"bound_events": 2}, "ambiguous binding"),
        ({"drop_repo_id": True}, "missing repository id (no inference)"),
        ({"semantic_bad": True}, "semantic identity mismatch"),
    ],
)
async def test_ambiguous_or_incomplete_records_are_refused(
    kwargs: dict[str, Any], label: str
) -> None:
    _, pipeline_id, _ = await _seed_historical(**kwargs)
    verifier = StubVerifier()
    assert await _run_recovery(pipeline_id, verifier) is False, label
    assert verifier.calls == [], label
    assert (await _stored_context(pipeline_id)).provenance is None, label


@pytest.mark.asyncio
async def test_failed_github_verification_recovers_nothing() -> None:
    _, pipeline_id, _ = await _seed_historical()
    verifier = StubVerifier(error=ProvenanceVerificationError("rejected", code="github_api_error"))
    assert await _run_recovery(pipeline_id, verifier) is False
    assert (await _stored_context(pipeline_id)).provenance is None


@pytest.mark.asyncio
async def test_recovery_does_not_touch_other_pipelines() -> None:
    _, target, _ = await _seed_historical()
    _, other, _ = await _seed_historical()
    assert await _run_recovery(target, StubVerifier()) is True
    assert (await _stored_context(other)).provenance is None
