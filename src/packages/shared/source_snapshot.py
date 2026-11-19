"""Immutable, source-bound snapshots for validation (ADR-0011, Phase 1.6).

Lifecycle:

1. `SnapshotService.prepare(token)` verifies the persisted provenance token, acquires the exact
   commit's tree through `GitHubGitSource`, and materializes it into a private, owner-only
   directory. Only then does it issue a `VerifiedSnapshot` capability and register it as live.
2. The sandbox consumes that capability. It re-verifies the stored digest and every file's Git
   blob ID, then copies the snapshot into a fresh, separate writable workspace. Only the writable
   workspace is ever mounted into Docker. The immutable base never leaves the host.
3. Leaving the `prepare` context removes the whole snapshot tree on success, error, timeout, and
   cancellation. A released handle can never be used again.

Immutability is enforced by digest and per-file object-ID verification on every use. Read-only
permission bits are defence in depth only and are not relied on.
"""

import hashlib
import os
import shutil
import stat
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError
from src.packages.sdk.github_git_source import (
    API_BASE_URL,
    FileEntry,
    GitHubGitSource,
    SourceAcquisitionError,
    tree_object_sha,
)
from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.persisted_provenance import ProvenanceRejected, VerifiedProvenance

logger = get_logger("akesis.source_snapshot")
_ISSUE_STATE = threading.local()
_ISSUED_SNAPSHOTS: dict[str, tuple[Any, ...]] = {}
_LOCK = threading.Lock()
_LIVE: dict[str, "_Record"] = {}
_READ_CHUNK = 1 << 16


class SnapshotPreparer(Protocol):
    """Anything that can prepare a verified snapshot for a provenance token."""

    def prepare(self, token: object) -> AbstractAsyncContextManager["VerifiedSnapshot"]: ...


class SnapshotRejected(Exception):
    """A snapshot handle or its contents failed a check. Carries a stable, non-sensitive code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VerifiedSnapshot:
    """Capability to validate against one exact, verified source snapshot.

    Constructible only through `_issue_snapshot`. Every construction, including
    `dataclasses.replace`, is checked against the issuance registry, so any altered field produces
    a forged, rejected object.
    """

    snapshot_id: str
    incident_id: str
    pipeline_id: str
    delivery_id: str
    event_repository_id: int
    source_repository_id: int
    source_full_name: str
    installation_id: int
    commit_sha: str
    source_branch: str
    root_tree_sha: str
    digest: str
    file_count: int
    total_bytes: int
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if getattr(_ISSUE_STATE, "active", False):
            return
        if _ISSUED_SNAPSHOTS.get(self.snapshot_id) != self._values():
            raise SnapshotRejected("Snapshot handles are issued only by preparation", code="forged")

    def _values(self) -> tuple[Any, ...]:
        return tuple(getattr(self, item.name) for item in fields(self))


def _issue_snapshot(**values: Any) -> VerifiedSnapshot:
    _ISSUE_STATE.active = True
    try:
        handle = VerifiedSnapshot(**values)
    finally:
        _ISSUE_STATE.active = False
    _ISSUED_SNAPSHOTS[handle.snapshot_id] = handle._values()
    return handle


@dataclass
class _Record:
    root: Path
    source_dir: Path
    manifest: list[FileEntry]
    digest: str
    expires_at: datetime
    state: str = "active"
    workspaces: set[Path] = field(default_factory=set)


def _safe_root() -> Path:
    root = Path(settings.snapshot_base_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise SnapshotRejected("Snapshot root must be an owner-only directory", code="root")
    if info.st_uid != os.getuid():
        raise SnapshotRejected("Snapshot root must be owned by this process", code="root")
    return root


def _check_dir(path: Path) -> None:
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise SnapshotRejected("Unexpected non-directory in snapshot tree", code="layout")


def _open_new(path: Path, executable: bool) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, 0o700 if executable else 0o600)


def _read_nofollow(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotRejected("Snapshot contains a non-regular file", code="layout")
        chunks = []
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()  # noqa: S324


def _digest(commit_sha: str, root_tree_sha: str, manifest: list[FileEntry]) -> str:
    hasher = hashlib.sha256()
    hasher.update(f"commit {commit_sha}\ntree {root_tree_sha}\n".encode())
    for entry in manifest:
        hasher.update(f"{entry.mode} {entry.blob_sha} {entry.path}\n".encode())
    return hasher.hexdigest()


def materialize(source_dir: Path, manifest: list[FileEntry], contents: dict[str, bytes]) -> None:
    """Write verified contents into an owner-only directory. Every path is created exclusively."""
    total = 0
    for entry in manifest:
        data = contents[entry.path]
        if _blob_sha(data) != entry.blob_sha:
            raise SnapshotRejected("Content does not match its object ID", code="integrity")
        total += len(data)
        parts = entry.path.split("/")
        for part in parts:
            # Defence in depth: the walk already validates, but the writer must not trust that.
            if part in {"", ".", ".."} or part.lower() == ".git" or "\\" in part:
                raise SnapshotRejected("Unsafe path component", code="invalid_path")
        current = source_dir
        for part in parts[:-1]:
            current = current / part
            if not current.exists():
                os.mkdir(current, 0o700)
            _check_dir(current)
        target = source_dir / entry.path
        fd = _open_new(target, executable=entry.mode == "100755")
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)
    # Permission bits are defence in depth only; integrity is verified on every use.
    for root, dirs, files in os.walk(source_dir):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o500)
        for name in files:
            full = os.path.join(root, name)
            mode = 0o500 if os.stat(full, follow_symlinks=False).st_mode & 0o100 else 0o400
            os.chmod(full, mode)
    os.chmod(source_dir, 0o500)


def verify_base(record: _Record) -> None:
    """Re-verify every file and the exact file set before any use of the immutable base."""
    expected = {entry.path: entry for entry in record.manifest}
    found: set[str] = set()
    for root, dirs, files in os.walk(record.source_dir, followlinks=False):
        for name in dirs:
            full = Path(root) / name
            if stat.S_ISLNK(os.lstat(full).st_mode):
                raise SnapshotRejected("Snapshot contains a symbolic link", code="modified")
        for name in files:
            full = Path(root) / name
            info = os.lstat(full)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise SnapshotRejected("Snapshot contains a non-regular file", code="modified")
            found.add(str(full.relative_to(record.source_dir)).replace(os.sep, "/"))
    if found != set(expected):
        raise SnapshotRejected("Snapshot file set was modified", code="modified")
    for path, entry in expected.items():
        content = _read_nofollow(record.source_dir / path)
        if _blob_sha(content) != entry.blob_sha:
            raise SnapshotRejected("Snapshot file was modified", code="modified")


def snapshot_matches_token(token: Any, snapshot: VerifiedSnapshot) -> bool:
    """True only if the snapshot describes exactly the identity the provenance token carries."""
    return all(
        (
            token.incident_id == snapshot.incident_id,
            token.pipeline_id == snapshot.pipeline_id,
            token.delivery_id == snapshot.delivery_id,
            token.event_repository_id == snapshot.event_repository_id,
            token.source_repository_id == snapshot.source_repository_id,
            f"{token.owner}/{token.name}" == snapshot.source_full_name,
            token.installation_id == snapshot.installation_id,
            token.head_sha == snapshot.commit_sha,
            token.head_branch == snapshot.source_branch,
        )
    )


def require_snapshot_bound(token: object, snapshot: object) -> None:
    """A provenance token alone is never enough: it must describe this exact snapshot."""
    if not isinstance(token, VerifiedProvenance):
        raise ProvenanceRejected("Verified provenance is required", code="missing")
    if not isinstance(snapshot, VerifiedSnapshot):
        raise ProvenanceRejected("A verified snapshot is required", code="missing")
    if not snapshot_matches_token(token, snapshot):
        raise ProvenanceRejected("Snapshot is not bound to this provenance", code="binding")


def _live_record(snapshot: object) -> tuple[VerifiedSnapshot, _Record]:
    if not isinstance(snapshot, VerifiedSnapshot):
        raise SnapshotRejected("A verified snapshot is required", code="missing")
    with _LOCK:
        record = _LIVE.get(snapshot.snapshot_id)
    if record is None or record.state != "active":
        raise SnapshotRejected("Snapshot handle is not live", code="released")
    if datetime.now(UTC) >= record.expires_at or datetime.now(UTC) >= snapshot.expires_at:
        raise SnapshotRejected("Snapshot handle has expired", code="expired")
    if record.digest != snapshot.digest:
        raise SnapshotRejected("Snapshot digest does not match", code="modified")
    return snapshot, record


def verify_snapshot(snapshot: object) -> VerifiedSnapshot:
    """Full re-verification of a live handle. Returns the handle; raises SnapshotRejected."""
    handle, record = _live_record(snapshot)
    verify_base(record)
    return handle


def create_workspace(snapshot: object) -> Path:
    """Copy a verified snapshot into a fresh, separate, writable workspace for one validation."""
    _, record = _live_record(snapshot)
    verify_base(record)
    workspace = record.root / "work" / uuid.uuid4().hex
    os.makedirs(workspace.parent, mode=0o700, exist_ok=True)
    os.mkdir(workspace, 0o700)
    with _LOCK:
        record.workspaces.add(workspace)
    try:
        for entry in record.manifest:
            data = _read_nofollow(record.source_dir / entry.path)
            if _blob_sha(data) != entry.blob_sha:
                raise SnapshotRejected("Snapshot file was modified", code="modified")
            target = workspace / entry.path
            os.makedirs(target.parent, mode=0o700, exist_ok=True)
            fd = _open_new(target, executable=entry.mode == "100755")
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            finally:
                os.close(fd)
    except BaseException:
        discard_workspace(workspace)
        raise
    return workspace


def _remove_tree(path: Path) -> None:
    """Remove a tree that may contain read-only directories, restoring owner write first."""
    if not path.exists():
        return
    for current, dirs, _files in os.walk(path, topdown=True):
        try:
            os.chmod(current, 0o700)
        except OSError:
            pass
        _ = dirs
    shutil.rmtree(path, ignore_errors=True)


def discard_workspace(workspace: Path) -> None:
    """Remove one writable workspace. Safe to call more than once."""
    with _LOCK:
        for record in _LIVE.values():
            record.workspaces.discard(workspace)
    _remove_tree(workspace)


@dataclass(frozen=True)
class WorkspaceChange:
    """One file that differs between a workspace and its snapshot. `mode` None means deleted."""

    path: str
    mode: str | None
    content: bytes | None
    blob_sha: str | None


def workspace_changes(snapshot: object, workspace: Path) -> list[WorkspaceChange]:
    """Every file a workspace adds, modifies or deletes relative to its verified snapshot.

    The workspace must belong to this live snapshot. Symbolic links, non-regular files, and
    `.git` or traversal components fail closed: their content is never read.
    """
    _, record = _live_record(snapshot)
    with _LOCK:
        owned = workspace in record.workspaces
    if not owned:
        raise SnapshotRejected("Workspace does not belong to this snapshot", code="binding")
    found: dict[str, tuple[str, Path]] = {}
    for root, dirs, files in os.walk(workspace, followlinks=False):
        for name in dirs + files:
            info = os.lstat(Path(root) / name)
            if stat.S_ISLNK(info.st_mode):
                raise SnapshotRejected("Workspace contains a symbolic link", code="symlink")
            if name in files and not stat.S_ISREG(info.st_mode):
                raise SnapshotRejected("Workspace contains a non-regular file", code="layout")
        for name in files:
            full = Path(root) / name
            relative = str(full.relative_to(workspace)).replace(os.sep, "/")
            for part in relative.split("/"):
                if part in {"", ".", ".."} or part.lower() == ".git" or "\\" in part:
                    raise SnapshotRejected("Unsafe path component", code="invalid_path")
            mode = "100755" if os.lstat(full).st_mode & 0o100 else "100644"
            found[relative] = (mode, full)
    expected = {entry.path: entry for entry in record.manifest}
    changes: list[WorkspaceChange] = []
    for path in sorted(set(found) | set(expected)):
        if path not in found:
            changes.append(WorkspaceChange(path=path, mode=None, content=None, blob_sha=None))
            continue
        mode, full = found[path]
        data = _read_nofollow(full)
        sha = _blob_sha(data)
        entry = expected.get(path)
        if entry is None or entry.blob_sha != sha or entry.mode != mode:
            changes.append(WorkspaceChange(path=path, mode=mode, content=data, blob_sha=sha))
    return changes


def _tree_sha(files: dict[str, tuple[str, str]]) -> str:
    """Git tree ID for a complete set of `path -> (mode, blob sha)` regular files."""
    root: dict[str, Any] = {}
    for path, value in files.items():
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def build(node: dict[str, Any]) -> str:
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append({"name": name, "mode": "040000", "sha": build(value)})
            else:
                entries.append({"name": name, "mode": value[0], "sha": value[1]})
        return tree_object_sha(entries)

    return build(root)


def tree_sha_after(snapshot: object, changes: list[WorkspaceChange]) -> str:
    """The exact root tree ID that the snapshot plus these changes must produce.

    The unchanged manifest is first rebuilt and checked against the verified root tree, so a
    remote tree can be compared byte-for-byte with what was validated locally.
    """
    handle, record = _live_record(snapshot)
    files = {entry.path: (entry.mode, entry.blob_sha) for entry in record.manifest}
    if _tree_sha(files) != handle.root_tree_sha:
        raise SnapshotRejected("Snapshot manifest does not rebuild its tree", code="integrity")
    for change in changes:
        if change.mode is None or change.blob_sha is None:
            files.pop(change.path, None)
        else:
            files[change.path] = (change.mode, change.blob_sha)
    return _tree_sha(files)


def _release(snapshot_id: str) -> None:
    with _LOCK:
        record = _LIVE.pop(snapshot_id, None)
    if record is None:
        return
    record.state = "released"
    _remove_tree(record.root)


class SnapshotService:
    """Prepares verified snapshots from GitHub. Credentials stay in memory and never reach disk."""

    def __init__(self, auth: Any = None, client_factory: Any = None) -> None:
        self._auth = auth
        self._client_factory = client_factory or self._default_client

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0)

    def _authenticator(self, client: httpx.AsyncClient) -> Any:
        if self._auth is not None:
            return self._auth
        if not settings.github_app_id or not settings.github_app_private_key:
            raise SnapshotRejected("Source authentication is not configured", code="unavailable")
        return GitHubAppAuth(settings.github_app_id, settings.github_app_private_key, client=client)

    @asynccontextmanager
    async def prepare(self, token: object) -> AsyncIterator[VerifiedSnapshot]:
        """Prepare one snapshot for a verified token. The snapshot is released on exit."""
        if not isinstance(token, VerifiedProvenance):
            raise SnapshotRejected("Verified provenance is required", code="missing")
        async with self._open_unchecked(token) as snapshot:
            yield snapshot

    @asynccontextmanager
    async def _open_unchecked(self, token: Any) -> AsyncIterator[VerifiedSnapshot]:
        """Preparation body. Private: `prepare` enforces the verified-token type first."""
        root = _safe_root()
        snapshot_id = uuid.uuid4().hex
        snapshot_root = root / snapshot_id
        snapshot_root.mkdir(mode=0o700)
        source_dir = snapshot_root / "source"
        source_dir.mkdir(mode=0o700)
        try:
            snapshot = await self._build(token, snapshot_id, snapshot_root, source_dir)
        except BaseException:
            _remove_tree(snapshot_root)
            raise
        try:
            yield snapshot
        finally:
            _release(snapshot_id)

    async def _build(
        self,
        token: VerifiedProvenance,
        snapshot_id: str,
        snapshot_root: Path,
        source_dir: Path,
    ) -> VerifiedSnapshot:
        try:
            async with self._client_factory() as client:
                auth = self._authenticator(client)
                installation = await auth.installation_token(token.installation_id)
                git = GitHubGitSource(
                    client,
                    installation.token,
                    max_files=settings.snapshot_max_files,
                    max_file_bytes=settings.snapshot_max_file_bytes,
                    max_total_bytes=settings.snapshot_max_total_bytes,
                )
                commit = await git.resolve_commit(
                    token.source_repository_id, f"{token.owner}/{token.name}", token.head_sha
                )
                manifest = await git.walk(token.source_repository_id, commit.tree_sha)
                contents: dict[str, bytes] = {}
                total = 0
                for entry in manifest:
                    if entry.path.rsplit("/", 1)[-1] == ".gitattributes":
                        raw = await git.read_blob(token.source_repository_id, entry)
                        if b"filter=" in raw:
                            raise SourceAcquisitionError(
                                "Git attribute filters are not supported", code="filter"
                            )
                        contents[entry.path] = raw
                    else:
                        contents[entry.path] = await git.read_blob(
                            token.source_repository_id, entry
                        )
                    total += len(contents[entry.path])
                    if total > settings.snapshot_max_total_bytes:
                        raise SourceAcquisitionError("Snapshot exceeds total size", code="limit")
        except SourceAcquisitionError as error:
            raise SnapshotRejected(
                "Source snapshot could not be verified", code=error.code
            ) from None
        except GitHubAppAuthenticationError as error:
            raise SnapshotRejected("Source authentication failed", code=error.code) from None
        except (httpx.HTTPError, SnapshotRejected) as error:
            if isinstance(error, SnapshotRejected):
                raise
            raise SnapshotRejected("Source snapshot unavailable", code="network") from None

        materialize(source_dir, manifest, contents)
        digest = _digest(commit.commit_sha, commit.tree_sha, manifest)
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=settings.snapshot_ttl_seconds)
        record = _Record(
            root=snapshot_root,
            source_dir=source_dir,
            manifest=manifest,
            digest=digest,
            expires_at=expires,
        )
        with _LOCK:
            _LIVE[snapshot_id] = record
        total_bytes = sum(len(v) for v in contents.values())
        logger.info(
            "source_snapshot_prepared",
            snapshot_id=snapshot_id,
            incident_id=token.incident_id,
            commit_sha=commit.commit_sha,
            file_count=len(manifest),
            total_bytes=total_bytes,
        )
        return _issue_snapshot(
            snapshot_id=snapshot_id,
            incident_id=token.incident_id,
            pipeline_id=token.pipeline_id,
            delivery_id=token.delivery_id,
            event_repository_id=token.event_repository_id,
            source_repository_id=token.source_repository_id,
            source_full_name=f"{token.owner}/{token.name}",
            installation_id=token.installation_id,
            commit_sha=commit.commit_sha,
            source_branch=token.head_branch,
            root_tree_sha=commit.tree_sha,
            digest=digest,
            file_count=len(manifest),
            total_bytes=total_bytes,
            created_at=now,
            expires_at=expires,
        )
