"""Write a remediation commit, branch and pull request to one verified repository (Phase 1.7).

Every call goes to the canonical GitHub API with a short-lived installation token held only in
memory and sent only as an `Authorization` header. Nothing is cloned, no Git process runs, and no
credential reaches a URL, argument, file or log.

Each created object is checked against what the caller expects: blob IDs are recomputed locally,
the tree must equal the locally computed tree, and the commit must carry that tree and the single
approved parent. Branches are only created, never moved, so an existing branch is never
overwritten.
"""

import base64
import hashlib
import re
from typing import Any
from urllib.parse import quote

import httpx

from src.packages.sdk.github_git_source import API_BASE_URL

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubWriteError(Exception):
    """A GitHub write or read failed or returned an unexpected object. Never carries credentials."""

    def __init__(self, message: str, *, code: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()  # noqa: S324


class GitHubRepositoryWriter:
    """Installation-scoped access to exactly one repository, addressed by ID and verified name."""

    def __init__(
        self, client: httpx.AsyncClient, token: str, *, repository_id: int, full_name: str
    ) -> None:
        if str(client.base_url).rstrip("/") != API_BASE_URL:
            raise GitHubWriteError("Canonical GitHub API required", code="untrusted_api")
        if not token:
            raise GitHubWriteError("Installation token required", code="unauthenticated")
        if (
            not _FULL_NAME_RE.fullmatch(full_name)
            or any(part in {".", ".."} for part in full_name.split("/"))
            or repository_id <= 0
        ):
            raise GitHubWriteError("Invalid repository identity", code="identity")
        self._client = client
        self._token = token
        self.repository_id = repository_id
        self.full_name = full_name

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                params=params,
                headers=self._headers(),
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise GitHubWriteError("GitHub unavailable", code="network") from None
        status = response.status_code
        if status >= 400:
            code = {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                422: "unprocessable",
                429: "rate_limited",
            }.get(status, "github_error")
            raise GitHubWriteError(
                f"GitHub {method} failed ({status})", code=code, status_code=status
            )
        try:
            return response.json()
        except ValueError:
            raise GitHubWriteError("Malformed GitHub response", code="malformed") from None

    def _repo(self, suffix: str) -> str:
        return f"/repos/{self.full_name}{suffix}"

    @staticmethod
    def _branch(name: str) -> str:
        """Git forbids these in ref names; refusing them keeps request paths inside the repo."""
        if not name or ".." in name or name.startswith("/") or name.endswith("/") or "\\" in name:
            raise GitHubWriteError("Invalid branch name", code="invalid_branch")
        return name

    @staticmethod
    def _sha(value: object) -> str:
        if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
            raise GitHubWriteError("Malformed object ID", code="malformed")
        return value

    async def get_repository(self) -> dict[str, Any]:
        """Canonical metadata, bound to the numeric ID and the expected full name."""
        data = await self._call("GET", f"/repositories/{self.repository_id}")
        if (
            not isinstance(data, dict)
            or data.get("id") != self.repository_id
            or data.get("full_name") != self.full_name
        ):
            raise GitHubWriteError("Repository identity mismatch", code="identity")
        return data

    async def branch_head(self, branch: str) -> str | None:
        """The commit a branch points to, or None if the branch does not exist."""
        try:
            data = await self._call(
                "GET", self._repo(f"/git/ref/heads/{quote(self._branch(branch), safe='/')}")
            )
        except GitHubWriteError as error:
            if error.code == "not_found":
                return None
            raise
        target = data.get("object") if isinstance(data, dict) else None
        if (
            not isinstance(target, dict)
            or data.get("ref") != f"refs/heads/{branch}"
            or target.get("type") != "commit"
        ):
            raise GitHubWriteError("Unexpected branch reference", code="malformed")
        return self._sha(target.get("sha"))

    async def create_blob(self, content: bytes) -> str:
        expected = _blob_sha(content)
        data = await self._call(
            "POST",
            self._repo("/git/blobs"),
            json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
        )
        if data.get("sha") != expected:
            raise GitHubWriteError("Created blob does not verify", code="integrity")
        return expected

    async def create_tree(
        self, base_tree: str, entries: list[dict[str, Any]], expected_sha: str
    ) -> str:
        data = await self._call(
            "POST",
            self._repo("/git/trees"),
            json={"base_tree": self._sha(base_tree), "tree": entries},
        )
        if data.get("sha") != expected_sha:
            raise GitHubWriteError(
                "Created tree does not match the validated tree", code="integrity"
            )
        return expected_sha

    async def get_commit(self, sha: str) -> tuple[str, list[str]]:
        """(tree, parents) of a commit object in this repository."""
        data = await self._call("GET", self._repo(f"/git/commits/{self._sha(sha)}"))
        return self._commit_shape(data, sha)

    def _commit_shape(self, data: Any, sha: str | None = None) -> tuple[str, list[str]]:
        tree = data.get("tree") if isinstance(data, dict) else None
        parents = data.get("parents") if isinstance(data, dict) else None
        if not isinstance(tree, dict) or not isinstance(parents, list):
            raise GitHubWriteError("Malformed commit", code="malformed")
        if sha is not None and data.get("sha") != sha:
            raise GitHubWriteError("Commit does not match the requested object", code="integrity")
        return self._sha(tree.get("sha")), [
            self._sha(item.get("sha") if isinstance(item, dict) else None) for item in parents
        ]

    async def create_commit(
        self, message: str, tree: str, parent: str, author: dict[str, str]
    ) -> str:
        data = await self._call(
            "POST",
            self._repo("/git/commits"),
            json={
                "message": message,
                "tree": self._sha(tree),
                "parents": [self._sha(parent)],
                "author": author,
                "committer": author,
            },
        )
        created_tree, parents = self._commit_shape(data)
        if created_tree != tree or parents != [parent]:
            raise GitHubWriteError("Created commit does not match", code="integrity")
        return self._sha(data.get("sha"))

    async def create_branch(self, branch: str, sha: str) -> bool:
        """Create `refs/heads/<branch>`. False if it already exists; it is never moved or forced."""
        try:
            data = await self._call(
                "POST",
                self._repo("/git/refs"),
                json={"ref": f"refs/heads/{self._branch(branch)}", "sha": self._sha(sha)},
            )
        except GitHubWriteError as error:
            if error.code == "unprocessable":  # GitHub: "Reference already exists"
                return False
            raise
        target = data.get("object") if isinstance(data, dict) else None
        if (
            data.get("ref") != f"refs/heads/{branch}"
            or not isinstance(target, dict)
            or target.get("sha") != sha
        ):
            raise GitHubWriteError("Created branch does not match", code="integrity")
        return True

    async def find_pull_requests(self, head_branch: str, base: str) -> list[dict[str, Any]]:
        """All pull requests (any state) from `<owner>:<head_branch>` into `base`."""
        owner = self.full_name.split("/", 1)[0]
        data = await self._call(
            "GET",
            self._repo("/pulls"),
            params={"head": f"{owner}:{head_branch}", "base": base, "state": "all"},
        )
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise GitHubWriteError("Malformed pull request listing", code="malformed")
        return data

    async def create_pull_request(
        self, title: str, body: str, head: str, base: str
    ) -> dict[str, Any]:
        data = await self._call(
            "POST",
            self._repo("/pulls"),
            json={"title": title, "body": body, "head": head, "base": base, "draft": False},
        )
        if not isinstance(data, dict):
            raise GitHubWriteError("Malformed pull request", code="malformed")
        return data
