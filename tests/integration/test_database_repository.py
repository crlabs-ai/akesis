import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.packages.database.repositories import ApprovalRepository
from src.packages.shared.config import settings
from src.packages.shared.models import ApprovalRecord, ApprovalStatus


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def sample_approval_record() -> ApprovalRecord:
    now = datetime.now(UTC)
    unique_suffix = f"{now.timestamp():.6f}".replace(".", "_")
    return ApprovalRecord(
        approval_id=f"appr_test_db_{unique_suffix}",
        incident_id="inc_db_01",
        proposal_id=f"prop_db_{unique_suffix}",
        commit_sha="112233445566",
        status=ApprovalStatus.PENDING,
        requested_at=now,
        expires_at=now + timedelta(hours=24),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_approval_creation_and_retrieval(
    session_factory: async_sessionmaker[AsyncSession],
    sample_approval_record: ApprovalRecord,
) -> None:
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        created = await repo.create_approval(sample_approval_record)
        assert created.approval_id == sample_approval_record.approval_id
        assert created.status == ApprovalStatus.PENDING

    # Verify retrieval in separate session (persistence across sessions)
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        fetched = await repo.get_approval(sample_approval_record.approval_id)
        assert fetched is not None
        assert fetched.approval_id == sample_approval_record.approval_id
        assert fetched.status == ApprovalStatus.PENDING

        by_proposal = await repo.get_by_proposal_id(sample_approval_record.proposal_id)
        assert by_proposal is not None
        assert by_proposal.approval_id == sample_approval_record.approval_id


@pytest.mark.asyncio
async def test_approval_state_transitions(
    session_factory: async_sessionmaker[AsyncSession],
    sample_approval_record: ApprovalRecord,
) -> None:
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        await repo.create_approval(sample_approval_record)

    # Approve transition
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        updated, is_dup = await repo.record_decision(
            sample_approval_record.approval_id,
            ApprovalStatus.APPROVED,
            reviewer="lead_dev",
            reason="Verified good",
        )
        assert updated is not None
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.decided_by == "lead_dev"
        assert is_dup is False

    # Duplicate decision idempotency
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        dup, is_dup = await repo.record_decision(
            sample_approval_record.approval_id,
            ApprovalStatus.APPROVED,
            reviewer="lead_dev",
        )
        assert dup is not None
        assert dup.status == ApprovalStatus.APPROVED
        assert is_dup is True

    # Conflicting decision rejection
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        conflict, is_dup = await repo.record_decision(
            sample_approval_record.approval_id,
            ApprovalStatus.REJECTED,
            reviewer="other_dev",
        )
        assert conflict is not None
        assert conflict.status == ApprovalStatus.APPROVED
        assert is_dup is False


@pytest.mark.asyncio
async def test_concurrent_approve_reject_race(
    session_factory: async_sessionmaker[AsyncSession],
    sample_approval_record: ApprovalRecord,
) -> None:
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        await repo.create_approval(sample_approval_record)

    # Simulate two concurrent database sessions racing in parallel tasks
    async def try_approve() -> tuple[ApprovalRecord | None, bool]:
        async with session_factory() as session:
            repo = ApprovalRepository(session)
            return await repo.record_decision(
                sample_approval_record.approval_id,
                ApprovalStatus.APPROVED,
                reviewer="dev1",
            )

    async def try_reject() -> tuple[ApprovalRecord | None, bool]:
        async with session_factory() as session:
            repo = ApprovalRepository(session)
            return await repo.record_decision(
                sample_approval_record.approval_id,
                ApprovalStatus.REJECTED,
                reviewer="dev2",
            )

    results = await asyncio.gather(try_approve(), try_reject())
    statuses = [r[0].status for r in results if r[0] is not None]

    # Exactly one decision is committed as the winner; both point to the winning state
    assert len(set(statuses)) == 1
    winner_status = statuses[0]
    assert winner_status in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED)


@pytest.mark.asyncio
async def test_approval_expiration(
    session_factory: async_sessionmaker[AsyncSession],
    sample_approval_record: ApprovalRecord,
) -> None:
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        await repo.create_approval(sample_approval_record)

    async with session_factory() as session:
        repo = ApprovalRepository(session)
        expired = await repo.expire_approval(sample_approval_record.approval_id)
        assert expired is True

    async with session_factory() as session:
        repo = ApprovalRepository(session)
        record = await repo.get_approval(sample_approval_record.approval_id)
        assert record is not None
        assert record.status == ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_invalid_approval_lookup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        res = await repo.get_approval("non_existent_approval_id")
        assert res is None
