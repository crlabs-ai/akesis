"""Immutable snapshot engine: materialization, integrity, lifecycle, and cleanup (ADR-0011).

These tests exercise the real materializer and lifecycle. Synthetic repositories come from
tests/support/fake_github.py, and the snapshot is prepared through the private body with a
synthetic token shape. Only the public `prepare` entry requires a database-issued token, and its
binding is covered in tests/integration/test_snapshot_boundary.py.
"""

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.packages.sdk.github_git_source import FileEntry
from src.packages.shared import source_snapshot as engine
from src.packages.shared.config import settings
from src.packages.shared.source_snapshot import (
    SnapshotRejected,
    SnapshotService,
    VerifiedSnapshot,
    create_workspace,
    discard_workspace,
    materialize,
    verify_snapshot,
)
from tests.support.fake_github import SYNTHETIC_TOKEN, FakeGitHub, FakeInstallationAuth, blob_id

REPO_ID = 1001
FULL = "crlabs-ai/akesis"
COMMIT = "a" * 40


def _token(**overrides: Any) -> Any:
    fields = {
        "incident_id": "inc_snap_1",
        "pipeline_id": "pipe_inc_snap_1",
        "delivery_id": "delivery-1",
        "event_repository_id": REPO_ID,
        "source_repository_id": REPO_ID,
        "owner": "crlabs-ai",
        "name": "akesis",
        "installation_id": 42,
        "head_sha": COMMIT,
        "head_branch": "main",
        "run_id": 7,
        "run_attempt": 1,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _fake(**overrides: Any) -> FakeGitHub:
    files = {
        "src/app.py": ("100644", b"print('ok')\n"),
        ".github/workflows/ci.yml": ("100644", b"name: CI\n"),
        "scripts/run.sh": ("100755", b"#!/bin/sh\necho ok\n"),
    }
    options: dict[str, Any] = {
        "repo_id": REPO_ID,
        "full_name": FULL,
        "files": files,
        "commit_sha": COMMIT,
    }
    options.update(overrides)
    return FakeGitHub(**options)


@pytest.fixture
def snapshot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "snapshots"
    monkeypatch.setattr(settings, "snapshot_base_dir", str(root))
    return root


async def _open(fake: FakeGitHub, token: Any | None = None) -> AsyncIterator[VerifiedSnapshot]:
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service._open_unchecked(token or _token()) as snapshot:
        yield snapshot


def _entries(
    paths: dict[str, bytes], modes: dict[str, str] | None = None
) -> tuple[list[FileEntry], dict[str, bytes]]:
    manifest = [
        FileEntry(path=path, mode=(modes or {}).get(path, "100644"), blob_sha=blob_id(content))
        for path, content in sorted(paths.items())
    ]
    return manifest, paths


@pytest.mark.asyncio
async def test_prepared_snapshot_is_bound_to_identity_and_commit(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        assert snapshot.incident_id == "inc_snap_1"
        assert snapshot.pipeline_id == "pipe_inc_snap_1"
        assert snapshot.delivery_id == "delivery-1"
        assert snapshot.source_repository_id == REPO_ID
        assert snapshot.source_full_name == FULL
        assert snapshot.installation_id == 42
        assert snapshot.commit_sha == COMMIT
        assert snapshot.source_branch == "main"
        assert snapshot.file_count == 3
        assert len(snapshot.digest) == 64
        verify_snapshot(snapshot)


@pytest.mark.asyncio
async def test_snapshot_directories_are_owner_only_and_contain_no_git_metadata(
    snapshot_root: Path,
) -> None:
    async for snapshot in _open(_fake()):
        source = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        assert not (source / ".git").exists()
        assert not any(part.lower() == ".git" for part in (p.name for p in source.rglob("*")))
        root_mode = stat.S_IMODE(os.lstat(snapshot_root).st_mode)
        assert root_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_credentials_never_reach_disk(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        workspace = create_workspace(snapshot)
        try:
            for root in (snapshot_root, workspace):
                for item in root.rglob("*"):
                    if item.is_file():
                        assert SYNTHETIC_TOKEN.encode() not in item.read_bytes()
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_executable_bit_is_preserved_only_for_100755(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        workspace = create_workspace(snapshot)
        try:
            assert os.lstat(workspace / "scripts/run.sh").st_mode & 0o100
            assert not os.lstat(workspace / "src/app.py").st_mode & 0o100
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_writable_workspace_is_separate_from_the_immutable_base(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        workspace = create_workspace(snapshot)
        try:
            assert workspace != base
            assert base not in workspace.parents and workspace not in base.parents
            (workspace / "src/app.py").write_text("changed\n")
            assert (base / "src/app.py").read_bytes() == b"print('ok')\n"
            verify_snapshot(snapshot)
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_shared_checkout_changes_cannot_affect_the_snapshot(
    snapshot_root: Path, tmp_path: Path
) -> None:
    shared = tmp_path / "shared-checkout"
    shared.mkdir()
    (shared / "src").mkdir()
    (shared / "src/app.py").write_text("tampered-shared-checkout\n")
    async for snapshot in _open(_fake()):
        # The snapshot is built from the API alone; the shared checkout is never read.
        workspace = create_workspace(snapshot)
        try:
            assert (workspace / "src/app.py").read_bytes() == b"print('ok')\n"
        finally:
            discard_workspace(workspace)


@pytest.mark.asyncio
async def test_modified_base_file_is_rejected_before_use(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        target = base / "src/app.py"
        os.chmod(target, 0o600)
        target.write_text("tampered\n")
        with pytest.raises(SnapshotRejected) as modified:
            create_workspace(snapshot)
        assert modified.value.code == "modified"
        with pytest.raises(SnapshotRejected) as verified:
            verify_snapshot(snapshot)
        assert verified.value.code == "modified"


@pytest.mark.asyncio
async def test_added_or_removed_base_files_are_rejected(snapshot_root: Path) -> None:
    async for snapshot in _open(_fake()):
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        for directory in (base, base / "src"):
            os.chmod(directory, 0o700)
        (base / "injected.py").write_text("x\n")
        with pytest.raises(SnapshotRejected) as added:
            verify_snapshot(snapshot)
        assert added.value.code == "modified"
        (base / "injected.py").unlink()
        (base / "src" / "app.py").unlink()
        with pytest.raises(SnapshotRejected) as removed:
            verify_snapshot(snapshot)
        assert removed.value.code == "modified"


@pytest.mark.asyncio
async def test_symlink_planted_in_base_is_rejected(snapshot_root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("HOST-SECRET")
    async for snapshot in _open(_fake()):
        base = next(snapshot_root.glob(f"{snapshot.snapshot_id}/source"))
        os.chmod(base, 0o700)
        os.symlink(secret, base / "leak.txt")
        with pytest.raises(SnapshotRejected) as planted:
            verify_snapshot(snapshot)
        assert planted.value.code == "modified"


@pytest.mark.asyncio
async def test_preparation_error_leaves_no_residue(snapshot_root: Path) -> None:
    fake = _fake()
    fake.tamper_blob_content[blob_id(b"print('ok')\n")] = b"print('evil')\n"
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    with pytest.raises(SnapshotRejected) as error:
        async with service._open_unchecked(_token()):
            pass  # pragma: no cover - never reached
    assert error.value.code == "integrity"
    assert list(snapshot_root.iterdir()) == []


@pytest.mark.asyncio
async def test_release_on_exit_removes_everything_and_the_handle_dies(snapshot_root: Path) -> None:
    fake = _fake()
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    async with service._open_unchecked(_token()) as snapshot:
        workspace = create_workspace(snapshot)
        assert workspace.exists()
    assert list(snapshot_root.iterdir()) == []
    with pytest.raises(SnapshotRejected) as released:
        create_workspace(snapshot)
    assert released.value.code == "released"


@pytest.mark.asyncio
async def test_cancellation_mid_validation_releases_the_snapshot(snapshot_root: Path) -> None:
    fake = _fake()
    service = SnapshotService(auth=FakeInstallationAuth(), client_factory=fake.client)
    started = asyncio.Event()

    async def hold() -> None:
        async with service._open_unchecked(_token()) as snapshot:
            create_workspace(snapshot)
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(hold())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(snapshot_root.iterdir()) == []


@pytest.mark.asyncio
async def test_expired_handle_is_rejected(snapshot_root: Path) -> None:
    from datetime import UTC, datetime, timedelta

    async for snapshot in _open(_fake()):
        record = engine._LIVE[snapshot.snapshot_id]
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(SnapshotRejected) as expired:
            create_workspace(snapshot)
        assert expired.value.code == "expired"


@pytest.mark.asyncio
async def test_altered_copies_of_an_issued_handle_are_forged(snapshot_root: Path) -> None:
    """dataclasses.replace copies the issuer state; the registry must still reject the change."""
    import dataclasses

    async for snapshot in _open(_fake()):
        for change in (
            {"incident_id": "inc_other"},
            {"source_full_name": "evil/repo"},
            {"digest": "0" * 64},
        ):
            with pytest.raises(SnapshotRejected) as forged:
                dataclasses.replace(snapshot, **cast(Any, change))
            assert forged.value.code == "forged"
        same = dataclasses.replace(snapshot)
        assert same == snapshot


def test_forged_handles_cannot_be_constructed() -> None:
    from datetime import UTC, datetime

    with pytest.raises(SnapshotRejected) as forged:
        VerifiedSnapshot(
            snapshot_id="never-issued",
            incident_id="i",
            pipeline_id="p",
            delivery_id="d",
            event_repository_id=1,
            source_repository_id=1,
            source_full_name="o/r",
            installation_id=1,
            commit_sha=COMMIT,
            source_branch="main",
            root_tree_sha=COMMIT,
            digest="0" * 64,
            file_count=0,
            total_bytes=0,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
        )
    assert forged.value.code == "forged"


@pytest.mark.asyncio
async def test_total_size_limit_is_enforced(
    snapshot_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "snapshot_max_total_bytes", 10)
    with pytest.raises(SnapshotRejected) as limit:
        async for _ in _open(_fake()):
            pass  # pragma: no cover
    assert limit.value.code == "limit"
    assert list(snapshot_root.iterdir()) == []


@pytest.mark.asyncio
async def test_file_count_limit_is_enforced(
    snapshot_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "snapshot_max_files", 1)
    with pytest.raises(SnapshotRejected) as limit:
        async for _ in _open(_fake()):
            pass  # pragma: no cover
    assert limit.value.code == "limit"


@pytest.mark.asyncio
async def test_git_lfs_policy_is_enforced_end_to_end(snapshot_root: Path) -> None:
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 9\n"
    fake = _fake(files={"model.bin": ("100644", pointer)})
    with pytest.raises(SnapshotRejected) as lfs:
        async for _ in _open(fake):
            pass  # pragma: no cover
    assert lfs.value.code == "lfs"


@pytest.mark.asyncio
async def test_attribute_filters_are_refused(snapshot_root: Path) -> None:
    fake = _fake(files={".gitattributes": ("100644", b"*.bin filter=lfs\n")})
    with pytest.raises(SnapshotRejected) as filtered:
        async for _ in _open(fake):
            pass  # pragma: no cover
    assert filtered.value.code == "filter"


def test_materializer_refuses_traversal_even_if_called_directly(tmp_path: Path) -> None:
    manifest, contents = _entries({"../escape.py": b"x"})
    with pytest.raises(SnapshotRejected) as traversal:
        materialize(tmp_path, manifest, contents)
    assert traversal.value.code == "invalid_path"
    assert not (tmp_path.parent / "escape.py").exists()


def test_materializer_never_follows_a_planted_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    os.symlink(outside, source / "src")
    manifest, contents = _entries({"src/app.py": b"x"})
    with pytest.raises(SnapshotRejected) as layout:
        materialize(source, manifest, contents)
    assert layout.value.code == "layout"
    assert list(outside.iterdir()) == []


def test_materializer_creates_exclusively_and_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "app.py").write_bytes(b"pre-existing\n")
    manifest, contents = _entries({"app.py": b"new\n"})
    with pytest.raises(FileExistsError):
        materialize(source, manifest, contents)
    assert (source / "app.py").read_bytes() == b"pre-existing\n"


@pytest.mark.asyncio
async def test_identical_content_has_identical_digest_and_changes_alter_it(
    snapshot_root: Path,
) -> None:
    first_digest = ""
    async for first in _open(_fake()):
        first_digest = first.digest
    async for second in _open(_fake()):
        assert second.digest == first_digest
    changed = _fake(
        files={
            "src/app.py": ("100644", b"different\n"),
            ".github/workflows/ci.yml": ("100644", b"name: CI\n"),
            "scripts/run.sh": ("100755", b"#!/bin/sh\necho ok\n"),
        }
    )
    async for third in _open(changed):
        assert third.digest != first_digest
