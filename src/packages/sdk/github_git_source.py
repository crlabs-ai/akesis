"""Acquire an exact commit's file tree from GitHub's Git Data API, with local integrity checks.

Strategy (ADR-0011): the Git Data API is used instead of `git clone` with credentials because
- the installation token never reaches process arguments, `.git/config`, or any workspace file;
- no repository-controlled filter, hook, or smudge code runs on the host;
- every tree and blob is hash-verified locally, so a truncated or substituted response fails.

The commit object is trusted to the installation-scoped API response over TLS. Trees and blobs are
checked against their Git object IDs, and the root tree must equal the commit's tree.

Every request goes to the canonical API by numeric repository ID, never to an arbitrary host.
"""

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE_URL = "https://api.github.com"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_NAME_FORBIDDEN = {"", ".", ".."}
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
_MAX_TREE_DEPTH = 64
_MAX_API_REQUESTS = 5000
_FILE_MODES = {"100644", "100755"}


class SourceAcquisitionError(Exception):
    """Source could not be acquired or verified. Carries a stable code and no credentials."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FileEntry:
    path: str
    mode: str  # "100644" or "100755" only
    blob_sha: str


@dataclass(frozen=True)
class ResolvedCommit:
    commit_sha: str
    tree_sha: str


def _git_object_sha(kind: bytes, body: bytes) -> str:
    return hashlib.sha1(b"%s %d\0" % (kind, len(body)) + body).hexdigest()  # noqa: S324


def tree_object_sha(entries: list[dict[str, str]]) -> str:
    """Recompute a Git tree ID from its entries, using Git's canonical ordering and mode format."""
    records: list[tuple[bytes, bytes]] = []
    for entry in entries:
        is_tree = entry["mode"] == "040000"
        name = entry["name"].encode()
        key = name + (b"/" if is_tree else b"")
        mode = b"40000" if is_tree else entry["mode"].encode()
        raw = mode + b" " + name + b"\0" + bytes.fromhex(entry["sha"])
        records.append((key, raw))
    body = b"".join(raw for _, raw in sorted(records, key=lambda item: item[0]))
    return _git_object_sha(b"tree", body)


def validate_component(name: str) -> None:
    """Reject path components that could escape the snapshot or touch Git metadata."""
    if name in _NAME_FORBIDDEN or "/" in name or "\\" in name or "\0" in name:
        raise SourceAcquisitionError("Invalid path component", code="invalid_path")
    if name.lower() == ".git":
        raise SourceAcquisitionError("Git metadata cannot be materialized", code="git_metadata")
    if len(name.encode("utf-8", errors="strict")) > 255:
        raise SourceAcquisitionError("Path component too long", code="invalid_path")


class GitHubGitSource:
    """Read-only, bounded access to one verified repository through the Git Data API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str = "",
        *,
        max_files: int,
        max_file_bytes: int,
        max_total_bytes: int,
    ) -> None:
        if str(client.base_url).rstrip("/") != API_BASE_URL:
            raise SourceAcquisitionError("Canonical GitHub API required", code="untrusted_api")
        self._client = client
        self._token = token
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._requests = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get(self, path: str) -> dict[str, Any]:
        self._requests += 1
        if self._requests > _MAX_API_REQUESTS:
            raise SourceAcquisitionError("API request budget exhausted", code="budget")
        try:
            response = await self._client.get(path, headers=self._headers(), follow_redirects=False)
        except httpx.RequestError:
            raise SourceAcquisitionError("GitHub source unavailable", code="network") from None
        if response.status_code in (404, 409, 422):
            raise SourceAcquisitionError("Source object not found", code="source_missing")
        if response.status_code >= 400:
            raise SourceAcquisitionError("GitHub source request failed", code="github_error")
        try:
            data = response.json()
        except ValueError:
            raise SourceAcquisitionError("Malformed GitHub response", code="malformed") from None
        if not isinstance(data, dict):
            raise SourceAcquisitionError("Malformed GitHub response", code="malformed")
        return data

    async def resolve_commit(
        self, repository_id: int, full_name: str, commit_sha: str
    ) -> ResolvedCommit:
        """Bind the repository ID to its expected name, then bind the commit SHA and its tree."""
        if not _SHA_RE.fullmatch(commit_sha):
            raise SourceAcquisitionError("Invalid full commit SHA", code="invalid_sha")
        repo = await self._get(f"/repositories/{repository_id}")
        if repo.get("id") != repository_id or repo.get("full_name") != full_name:
            raise SourceAcquisitionError("Repository identity mismatch", code="identity")
        commit = await self._get(f"/repositories/{repository_id}/git/commits/{commit_sha}")
        tree = commit.get("tree")
        url = commit.get("url")
        if isinstance(url, str) and f"/repos/{full_name}/" not in url:
            raise SourceAcquisitionError("Commit belongs to another repository", code="identity")
        if (
            commit.get("sha") != commit_sha
            or not isinstance(tree, dict)
            or not isinstance(tree.get("sha"), str)
            or not _SHA_RE.fullmatch(tree["sha"])
        ):
            raise SourceAcquisitionError("Commit does not match the verified SHA", code="sha")
        return ResolvedCommit(commit_sha=commit_sha, tree_sha=tree["sha"])

    async def _read_tree(self, repository_id: int, tree_sha: str) -> list[dict[str, str]]:
        data = await self._get(f"/repositories/{repository_id}/git/trees/{tree_sha}")
        if data.get("truncated") is True:
            raise SourceAcquisitionError("Git tree response is truncated", code="truncated")
        if data.get("sha") != tree_sha or not isinstance(data.get("tree"), list):
            raise SourceAcquisitionError("Tree does not match the requested object", code="sha")
        entries: list[dict[str, str]] = []
        for raw in data["tree"]:
            if not isinstance(raw, dict):
                raise SourceAcquisitionError("Malformed tree entry", code="malformed")
            name, mode, kind, sha = (
                raw.get("path"),
                raw.get("mode"),
                raw.get("type"),
                raw.get("sha"),
            )
            if (
                not isinstance(name, str)
                or not isinstance(mode, str)
                or not isinstance(kind, str)
                or not isinstance(sha, str)
                or not _SHA_RE.fullmatch(sha)
            ):
                raise SourceAcquisitionError("Malformed tree entry", code="malformed")
            entries.append({"name": name, "mode": mode, "type": kind, "sha": sha})
        if tree_object_sha(entries) != tree_sha:
            raise SourceAcquisitionError("Tree object ID does not verify", code="integrity")
        return entries

    async def walk(self, repository_id: int, root_tree_sha: str) -> list[FileEntry]:
        """Traverse the complete tree. Each node is verified; unsupported entries fail closed."""
        files: list[FileEntry] = []
        seen_paths: set[str] = set()
        seen_folded: set[str] = set()
        stack: list[tuple[str, str, int]] = [("", root_tree_sha, 0)]
        while stack:
            prefix, tree_sha, depth = stack.pop()
            if depth > _MAX_TREE_DEPTH:
                raise SourceAcquisitionError("Tree depth exceeds limit", code="limit")
            for entry in await self._read_tree(repository_id, tree_sha):
                name = entry["name"]
                validate_component(name)
                path = f"{prefix}{name}"
                mode, kind = entry["mode"], entry["type"]
                if mode == "040000" and kind == "tree":
                    stack.append((f"{path}/", entry["sha"], depth + 1))
                    continue
                if mode == "120000":
                    raise SourceAcquisitionError(
                        "Symbolic links are not materialized", code="symlink"
                    )
                if mode == "160000" or kind == "commit":
                    raise SourceAcquisitionError("Submodules are not supported", code="gitlink")
                if mode not in _FILE_MODES or kind != "blob":
                    raise SourceAcquisitionError("Unsupported Git object mode", code="mode")
                if path in seen_paths or path.casefold() in seen_folded:
                    raise SourceAcquisitionError("Duplicate or colliding path", code="collision")
                seen_paths.add(path)
                seen_folded.add(path.casefold())
                files.append(FileEntry(path=path, mode=mode, blob_sha=entry["sha"]))
                if len(files) > self._max_files:
                    raise SourceAcquisitionError("File count exceeds limit", code="limit")
        return sorted(files, key=lambda item: item.path)

    async def read_blob(self, repository_id: int, entry: FileEntry) -> bytes:
        """Fetch one blob from the verified repository and verify it against its object ID."""
        data = await self._get(f"/repositories/{repository_id}/git/blobs/{entry.blob_sha}")
        if data.get("sha") != entry.blob_sha or data.get("encoding") != "base64":
            raise SourceAcquisitionError("Blob does not match the requested object", code="sha")
        content_b64 = data.get("content")
        if not isinstance(content_b64, str):
            raise SourceAcquisitionError("Malformed blob response", code="malformed")
        try:
            content = base64.b64decode(content_b64, validate=True)
        except ValueError:
            raise SourceAcquisitionError("Malformed blob encoding", code="malformed") from None
        if len(content) > self._max_file_bytes:
            raise SourceAcquisitionError("File exceeds size limit", code="limit")
        if data.get("size") != len(content):
            raise SourceAcquisitionError("Blob size does not verify", code="integrity")
        if _git_object_sha(b"blob", content) != entry.blob_sha:
            raise SourceAcquisitionError("Blob object ID does not verify", code="integrity")
        if content.startswith(_LFS_POINTER_PREFIX):
            # LFS policy: pointers are never validated as if they were the real asset.
            raise SourceAcquisitionError("Git LFS content is not supported", code="lfs")
        return content
