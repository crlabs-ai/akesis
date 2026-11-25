"""Synthetic GitHub REST and Git Data API for tests. No network: httpx.MockTransport only.

Trees and blobs carry real Git object IDs, so the production verification code runs unchanged.
Every knob below models one adversarial or broken response that the production code must reject.
"""

import base64
import hashlib
import json
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
    # Write side (Phase 1.7). Branch name -> head commit; the default branch starts at commit_sha.
    default_branch: str = "main"
    fork: bool = False
    archived: bool = False
    refs: dict[str, str] = field(default_factory=dict)
    fail: dict[str, int] = field(default_factory=dict)  # operation -> HTTP status to return
    tamper_created_blob: bool = False
    tamper_created_tree: bool = False
    move_refs_on_commit: dict[str, str] = field(default_factory=dict)  # race: applied on commit
    move_refs_on_branch: dict[str, str] = field(default_factory=dict)  # race: on branch creation
    pulls: list[dict[str, Any]] = field(default_factory=list)
    commits: dict[str, tuple[str, list[str]]] = field(default_factory=dict)
    writes: list[tuple[str, str, Any]] = field(default_factory=list)
    authorizations: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    _objects: dict[str, Any] = field(default_factory=dict, repr=False)
    _blobs: dict[str, bytes] = field(default_factory=dict, repr=False)
    root_tree_sha: str = ""

    def __post_init__(self) -> None:
        self.root_tree_sha = self._build(self._nest(), "")
        self.refs.setdefault(self.default_branch, self.commit_sha)
        self.commits[self.commit_sha] = (self.root_tree_sha, [])

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
        self.authorizations.append(request.headers.get("authorization", ""))
        repo_prefix = f"/repositories/{self.repo_id}"
        if path.startswith(f"/repos/{self.full_name}/"):
            return self._handle_repo(request, path[len(f"/repos/{self.full_name}") :])
        if path == repo_prefix:
            if "repo" in self.fail:
                return httpx.Response(self.fail["repo"], json={"message": "failure"})
            reported = self.repo_id + 1 if self.repo_id_mismatch else self.repo_id
            return httpx.Response(
                200,
                json={
                    "id": reported,
                    "full_name": self.full_name,
                    "fork": self.fork,
                    "archived": self.archived,
                    "default_branch": self.default_branch,
                },
            )
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

    # ---- write side ------------------------------------------------------------------------

    def _flatten(self, tree_sha: str, prefix: str = "") -> dict[str, tuple[str, str]]:
        files: dict[str, tuple[str, str]] = {}
        for entry in self._objects[tree_sha]:
            path = f"{prefix}{entry['name']}"
            if entry["mode"] == "040000":
                files.update(self._flatten(entry["sha"], f"{path}/"))
            else:
                files[path] = (entry["mode"], entry["sha"])
        return files

    def _store_flat(self, files: dict[str, tuple[str, str]]) -> str:
        root: dict[str, Any] = {}
        for path, value in files.items():
            node = root
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

        def build(node: dict[str, Any]) -> str:
            entries: list[dict[str, str]] = []
            for name, value in node.items():
                if isinstance(value, dict):
                    entries.append(
                        {"name": name, "mode": "040000", "type": "tree", "sha": build(value)}
                    )
                else:
                    entries.append(
                        {"name": name, "mode": value[0], "type": "blob", "sha": value[1]}
                    )
            sha = tree_object_sha(entries)
            self._objects[sha] = entries
            return sha

        return build(root)

    def _handle_repo(self, request: httpx.Request, rest: str) -> httpx.Response:
        method = request.method
        body: Any = json.loads(request.content) if request.content else None
        self.writes.append((method, rest, body))
        operation = {
            ("GET", "/git/ref/"): "ref_read",
            ("POST", "/git/blobs"): "blob",
            ("POST", "/git/trees"): "tree",
            ("POST", "/git/commits"): "commit",
            ("GET", "/git/commits/"): "commit_read",
            ("POST", "/git/refs"): "ref",
            ("GET", "/pulls"): "pull_list",
            ("POST", "/pulls"): "pull_create",
        }
        name = next(
            (
                op
                for (verb, prefix), op in operation.items()
                if verb == method and rest.startswith(prefix)
            ),
            None,
        )
        if name is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if name in self.fail:
            return httpx.Response(self.fail[name], json={"message": "failure"})
        if name == "ref_read":
            branch = rest[len("/git/ref/heads/") :]
            if branch not in self.refs:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                200,
                json={
                    "ref": f"refs/heads/{branch}",
                    "object": {"type": "commit", "sha": self.refs[branch]},
                },
            )
        if name == "blob":
            content = base64.b64decode(body["content"])
            sha = blob_id(content)
            self._blobs[sha] = content
            return httpx.Response(201, json={"sha": "e" * 40 if self.tamper_created_blob else sha})
        if name == "tree":
            files = self._flatten(body["base_tree"])
            for entry in body["tree"]:
                if entry["sha"] is None:
                    if entry["path"] not in files:
                        return httpx.Response(422, json={"message": "path not found"})
                    files.pop(entry["path"])
                else:
                    files[entry["path"]] = (entry["mode"], entry["sha"])
            sha = self._store_flat(files)
            return httpx.Response(201, json={"sha": "d" * 40 if self.tamper_created_tree else sha})
        if name == "commit":
            sha = hashlib.sha1(json.dumps([body, len(self.commits)]).encode()).hexdigest()  # noqa: S324
            self.commits[sha] = (body["tree"], list(body["parents"]))
            self.refs.update(self.move_refs_on_commit)
            return httpx.Response(
                201,
                json={
                    "sha": sha,
                    "tree": {"sha": body["tree"]},
                    "parents": [{"sha": parent} for parent in body["parents"]],
                },
            )
        if name == "commit_read":
            sha = rest[len("/git/commits/") :]
            if sha not in self.commits:
                return httpx.Response(404, json={"message": "Not Found"})
            tree, parents = self.commits[sha]
            return httpx.Response(
                200,
                json={
                    "sha": sha,
                    "tree": {"sha": tree},
                    "parents": [{"sha": parent} for parent in parents],
                },
            )
        if name == "ref":
            branch = body["ref"][len("refs/heads/") :]
            if branch in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[branch] = body["sha"]
            self.refs.update(self.move_refs_on_branch)
            return httpx.Response(
                201,
                json={"ref": body["ref"], "object": {"type": "commit", "sha": body["sha"]}},
            )
        if name == "pull_list":
            owner, head = request.url.params["head"].split(":", 1)
            base = request.url.params["base"]
            matching = [
                pull
                for pull in self.pulls
                if owner == self.full_name.split("/")[0]
                and pull["head"]["ref"] == head
                and pull["base"]["ref"] == base
            ]
            return httpx.Response(200, json=matching)
        # pull_create
        if body["head"] not in self.refs:
            return httpx.Response(422, json={"message": "head does not exist"})
        number = len(self.pulls) + 1
        pull = {
            "number": number,
            "state": "open",
            "html_url": f"https://github.com/{self.full_name}/pull/{number}",
            "title": body["title"],
            "body": body["body"],
            "head": {
                "ref": body["head"],
                "sha": self.refs[body["head"]],
                "repo": {"id": self.repo_id},
            },
            "base": {"ref": body["base"], "repo": {"id": self.repo_id}},
        }
        self.pulls.append(pull)
        return httpx.Response(201, json=pull)


class FakeInstallationAuth:
    """Synthetic installation token issuer. Records calls and scopes; never contacts GitHub."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.scopes: list[dict[str, Any]] = []

    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append(installation_id)
        self.scopes.append({"repository_ids": repository_ids, "permissions": permissions})

        class _Token:
            token = SYNTHETIC_TOKEN
            expires_at = None

        return _Token()
