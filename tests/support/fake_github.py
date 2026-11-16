"""Synthetic GitHub Git Data API for tests. No network: httpx.MockTransport only.

Trees and blobs carry real Git object IDs, so the production verification code runs unchanged.
Every knob below models one adversarial or broken response that the production code must reject.
"""

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.packages.sdk.github_git_source import API_BASE_URL, tree_object_sha

SYNTHETIC_TOKEN = "ghs_synthetic_test_token"  # noqa: S105 - synthetic test credential


def blob_id(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()  # noqa: S324


@dataclass
class FakeGitHub:
    repo_id: int
    full_name: str
    files: dict[str, tuple[str, bytes]]  # path -> (mode, content); gitlinks use content=b""
    commit_sha: str = "a" * 40
    truncate_tree: bool = False
    commit_repo_mismatch: bool = False
    wrong_commit_sha: bool = False
    missing_commit: bool = False
    tamper_blob_content: dict[str, bytes] = field(default_factory=dict)  # blob sha -> served bytes
    tamper_tree_listing: bool = False
    repo_id_mismatch: bool = False
    requests: list[str] = field(default_factory=list)
    _objects: dict[str, Any] = field(default_factory=dict, repr=False)
    _blobs: dict[str, bytes] = field(default_factory=dict, repr=False)
    root_tree_sha: str = ""

    def __post_init__(self) -> None:
        self.root_tree_sha = self._build(self._nest(), "")

    def _nest(self) -> dict[str, Any]:
        root: dict[str, Any] = {}
        for path, (mode, content) in self.files.items():
            parts = path.split("/")
            node = root
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = (mode, content)
        return root

    def _build(self, node: dict[str, Any], label: str) -> str:
        entries: list[dict[str, str]] = []
        for name, value in node.items():
            if isinstance(value, dict):
                sub = self._build(value, f"{label}{name}/")
                entries.append({"name": name, "mode": "040000", "type": "tree", "sha": sub})
                continue
            mode, content = value
            if mode == "160000":
                sha = hashlib.sha1(name.encode()).hexdigest()  # noqa: S324
                entries.append({"name": name, "mode": mode, "type": "commit", "sha": sha})
                continue
            sha = blob_id(content)
            self._blobs[sha] = content
            entries.append({"name": name, "mode": mode, "type": "blob", "sha": sha})
        tree_sha = tree_object_sha(entries)
        self._objects[tree_sha] = entries
        return tree_sha

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=API_BASE_URL, transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        repo_prefix = f"/repositories/{self.repo_id}"
        if path == repo_prefix:
            reported = self.repo_id + 1 if self.repo_id_mismatch else self.repo_id
            return httpx.Response(200, json={"id": reported, "full_name": self.full_name})
        if path.startswith("/repositories/") and not path.startswith(repo_prefix):
            return httpx.Response(404, json={"message": "Not Found"})
        if path == f"{repo_prefix}/git/commits/{self.commit_sha}":
            if self.missing_commit:
                return httpx.Response(422, json={"message": "No commit found"})
            repo_full = "other/elsewhere" if self.commit_repo_mismatch else self.full_name
            sha = "b" * 40 if self.wrong_commit_sha else self.commit_sha
            url = f"{API_BASE_URL}/repos/{repo_full}/git/commits/{sha}"
            return httpx.Response(
                200,
                json={"sha": sha, "url": url, "tree": {"sha": self.root_tree_sha}},
            )
        if path.startswith(f"{repo_prefix}/git/trees/"):
            tree_sha = path.rsplit("/", 1)[-1]
            entries = self._objects.get(tree_sha)
            if entries is None:
                return httpx.Response(404, json={"message": "Not Found"})
            # GitHub's tree schema names the entry "path", not "name".
            listing = [
                {"path": e["name"], "mode": e["mode"], "type": e["type"], "sha": e["sha"]}
                for e in entries
            ]
            if self.tamper_tree_listing and listing:
                listing[0]["sha"] = "c" * 40
            return httpx.Response(
                200,
                json={"sha": tree_sha, "tree": listing, "truncated": self.truncate_tree},
            )
        if path.startswith(f"{repo_prefix}/git/blobs/"):
            blob = path.rsplit("/", 1)[-1]
            content = self._blobs.get(blob)
            if content is None:
                return httpx.Response(404, json={"message": "Not Found"})
            body = self.tamper_blob_content.get(blob, content)
            return httpx.Response(
                200,
                json={
                    "sha": blob,
                    "size": len(body),
                    "encoding": "base64",
                    "content": base64.b64encode(body).decode(),
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})


class FakeInstallationAuth:
    """Synthetic installation token issuer. Records calls; never contacts GitHub."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def installation_token(self, installation_id: int) -> Any:
        self.calls.append(installation_id)

        class _Token:
            token = SYNTHETIC_TOKEN
            expires_at = None

        return _Token()
