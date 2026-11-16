"""Snapshot capability boundary with real persisted provenance (ADR-0011, Phase 1.6).

Tokens come from the database-backed verifier. Source comes from the synthetic GitHub API. The
temporary remediation hold stays active, so every valid path must stop at the hold.
"""

from pathlib import Path
from typing import Any

import pytest

from src.packages.sdk.sandbox_runner import DockerSandboxRunner, SandboxWorkspaceError
from src.packages.shared.config import settings
from src.packages.shared.models import ValidationCommand
from src.packages.shared.persisted_provenance import ProvenanceRejected, verify_context_provenance
from src.packages.shared.provenance import UnverifiedProvenanceError
from src.packages.shared.source_snapshot import (
    SnapshotRejected,
    SnapshotService,
    create_workspace,
)
from tests.integration.seeding import seed_incident
from tests.support.fake_github import FakeGitHub, FakeInstallationAuth, blob_id


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


def _github(commit_sha: str, **overrides: Any) -> FakeGitHub:
    options: dict[str, Any] = {
        "repo_id": 1001,
        "full_name": "crlabs-ai/akesis",
        "files": {"src.py": ("100644", b"x = 1\n")},
        "commit_sha": commit_sha,
    }
    options.update(overrides)
    return FakeGitHub(**options)


def _service(fake: FakeGitHub) -> SnapshotService:
    return SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)


@pytest.mark.asyncio
async def test_prepared_snapshot_is_bound_to_the_persisted_identity(snapshot_root: Path) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    async with _service(_github(seeded.context.commit_sha)).prepare(token) as snapshot:
        assert snapshot.incident_id == token.incident_id
        assert snapshot.pipeline_id == token.pipeline_id
        assert snapshot.delivery_id == token.delivery_id
        assert snapshot.source_repository_id == token.source_repository_id
        assert snapshot.commit_sha == token.head_sha
        assert snapshot.installation_id == token.installation_id


@pytest.mark.asyncio
async def test_valid_binding_stops_at_the_hold_and_creates_no_workspace(
    snapshot_root: Path,
) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    async with _service(_github(seeded.context.commit_sha)).prepare(token) as snapshot:
        with pytest.raises(UnverifiedProvenanceError):
            await DockerSandboxRunner().run_validation(
                snapshot, "", ValidationCommand.PYTEST, provenance=token
            )
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        assert base.exists()
        assert not (snapshot_root / snapshot.snapshot_id / "work").exists()


@pytest.mark.asyncio
async def test_snapshot_from_another_incident_is_rejected(snapshot_root: Path) -> None:
    first = await seed_incident("valid")
    second = await seed_incident("valid")
    first_token = await verify_context_provenance(first.context)
    second_token = await verify_context_provenance(second.context)
    async with _service(_github(first.context.commit_sha)).prepare(first_token) as snapshot:
        with pytest.raises(ProvenanceRejected) as foreign:
            await DockerSandboxRunner().run_validation(
                snapshot, "", ValidationCommand.PYTEST, provenance=second_token
            )
    assert foreign.value.code == "binding"


@pytest.mark.asyncio
async def test_snapshot_from_another_repository_is_rejected(snapshot_root: Path) -> None:
    this_repo = await seed_incident("valid")
    other_repo = await seed_incident("valid", repo_id=2002, full_name="crlabs-ai/other")
    this_token = await verify_context_provenance(this_repo.context)
    other_token = await verify_context_provenance(other_repo.context)
    fake = FakeGitHub(
        repo_id=2002,
        full_name="crlabs-ai/other",
        files={"src.py": ("100644", b"x = 1\n")},
        commit_sha=other_repo.context.commit_sha,
    )
    async with _service(fake).prepare(other_token) as foreign:
        with pytest.raises(ProvenanceRejected) as mismatch:
            await DockerSandboxRunner().run_validation(
                foreign, "", ValidationCommand.PYTEST, provenance=this_token
            )
    assert mismatch.value.code == "binding"


@pytest.mark.asyncio
async def test_snapshot_bound_to_another_commit_is_rejected(snapshot_root: Path) -> None:
    seeded = await seed_incident("valid")
    other = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    other_token = await verify_context_provenance(other.context)
    async with _service(_github(seeded.context.commit_sha)).prepare(token) as snapshot:
        with pytest.raises(ProvenanceRejected) as mismatch:
            await DockerSandboxRunner().run_validation(
                snapshot, "", ValidationCommand.PYTEST, provenance=other_token
            )
    assert mismatch.value.code == "binding"


@pytest.mark.asyncio
async def test_modified_snapshot_is_rejected_before_any_workspace_copy(snapshot_root: Path) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    async with _service(_github(seeded.context.commit_sha)).prepare(token) as snapshot:
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        target = base / "src.py"
        target.chmod(0o600)
        target.write_text("tampered\n")
        with pytest.raises(SandboxWorkspaceError):
            await DockerSandboxRunner()._run_validation_unchecked(
                snapshot, "", ValidationCommand.PYTEST
            )
        assert not (snapshot_root / snapshot.snapshot_id / "work").exists()


@pytest.mark.asyncio
async def test_released_snapshot_handle_is_unusable(snapshot_root: Path) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    async with _service(_github(seeded.context.commit_sha)).prepare(token) as snapshot:
        pass
    assert list(snapshot_root.iterdir()) == []
    with pytest.raises(SnapshotRejected) as released:
        create_workspace(snapshot)
    assert released.value.code == "released"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"missing_commit": True}, "source_missing"),
        ({"wrong_commit_sha": True}, "sha"),
        ({"repo_id_mismatch": True}, "identity"),
        ({"commit_repo_mismatch": True}, "identity"),
        ({"truncate_tree": True}, "truncated"),
    ],
)
async def test_source_identity_failures_surface_as_safe_codes(
    snapshot_root: Path, overrides: dict[str, Any], code: str
) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    with pytest.raises(SnapshotRejected) as refused:
        async with _service(_github(seeded.context.commit_sha, **overrides)).prepare(token):
            pass  # pragma: no cover
    assert refused.value.code == code
    assert list(snapshot_root.iterdir()) == []


@pytest.mark.asyncio
async def test_failed_preparation_leaves_no_residue_on_disk(snapshot_root: Path) -> None:
    seeded = await seed_incident("valid")
    token = await verify_context_provenance(seeded.context)
    fake = _github(seeded.context.commit_sha)
    fake.tamper_blob_content[blob_id(b"x = 1\n")] = b"evil\n"
    with pytest.raises(SnapshotRejected):
        async with _service(fake).prepare(token):
            pass  # pragma: no cover
    assert list(snapshot_root.iterdir()) == []
