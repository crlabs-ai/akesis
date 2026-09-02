import io
import zipfile
from typing import Any, Protocol, cast

import httpx

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger

logger = get_logger("akesis.github_client")


class GitHubAPIError(Exception):
    """Base exception for GitHub API communication errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubAPIError):
    """Exception raised when GitHub credentials or tokens are invalid."""

    pass


class GitHubPermissionError(GitHubAPIError):
    """Exception raised when token lacks necessary permissions on repository."""

    pass


class GitHubRateLimitError(GitHubAPIError):
    """Exception raised when GitHub API rate limit is exceeded."""

    pass


class GitHubResourceNotFoundError(GitHubAPIError):
    """Exception raised when requested GitHub resource is not found."""

    pass


class GitHubConflictError(GitHubAPIError):
    """Exception raised on 409 Conflict or 422 Unprocessable Entity."""

    pass


class GitHubClientProtocol(Protocol):
    """Interface for GitHub API interactions."""

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        """Fetches workflow run metadata by ID."""
        ...

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str:
        """Retrieves raw log text of a completed workflow run."""
        ...

    async def get_commit(self, owner: str, repo: str, commit_sha: str) -> dict[str, Any]:
        """Fetches commit metadata from GitHub repository."""
        ...

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        """Creates a Pull Request on target repository."""
        ...

    async def find_pull_request(
        self,
        owner: str,
        repo: str,
        head_branch: str,
        base_branch: str,
    ) -> dict[str, Any] | None:
        """Looks up existing open Pull Request for branch pair."""
        ...


class GitHubClient:
    """Async HTTP client for communicating with GitHub REST API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.token = token if token is not None else settings.github_token
        self.base_url = (base_url or settings.github_api_url).rstrip("/")
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _handle_error(self, err: httpx.HTTPStatusError, context_msg: str) -> None:
        status = err.response.status_code
        if status in (401,):
            raise GitHubAuthError(
                f"{context_msg}: Unauthorized (HTTP 401)", status_code=401
            ) from err
        if status == 429:
            raise GitHubRateLimitError(
                f"{context_msg}: Rate limit exceeded (HTTP 429)", status_code=429
            ) from err
        if status == 403:
            if "permission" in err.response.text.lower():
                raise GitHubPermissionError(
                    f"{context_msg}: Permission denied (HTTP 403)", status_code=403
                ) from err
            raise GitHubRateLimitError(
                f"{context_msg}: Rate limit exceeded or forbidden (HTTP 403)", status_code=403
            ) from err
        if status == 404:
            raise GitHubResourceNotFoundError(
                f"{context_msg}: Resource not found (HTTP 404)", status_code=404
            ) from err
        if status in (409, 422):
            raise GitHubConflictError(
                f"{context_msg}: Conflict or unprocessable entity (HTTP {status})",
                status_code=status,
            ) from err
        raise GitHubAPIError(f"{context_msg}: HTTP {status}", status_code=status) from err

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        """Fetches workflow run metadata by ID."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.get(url, headers=self._get_headers())
                res.raise_for_status()
                return cast(dict[str, Any], res.json())
            except httpx.HTTPStatusError as err:
                self._handle_error(err, f"Failed to get workflow run {run_id}")
                return {}

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str:
        """Retrieves raw log text of a completed workflow run, unzipping if returned as archive."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            try:
                res = await client.get(url, headers=self._get_headers())
                res.raise_for_status()

                # If GitHub returns a ZIP archive of individual step logs
                if res.content.startswith(b"PK\x03\x04"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                            log_parts: list[str] = []
                            for name in sorted(z.namelist()):
                                if name.endswith("/") or not name.endswith(".txt"):
                                    continue
                                info = z.getinfo(name)
                                if info.is_dir():
                                    continue
                                txt = z.read(name).decode("utf-8", errors="replace")
                                log_parts.append(f"=== {name} ===\n{txt}")
                            return "\n\n".join(log_parts)
                    except zipfile.BadZipFile as err:
                        logger.error(
                            "corrupted_workflow_logs_archive", run_id=run_id, error=str(err)
                        )
                        raise GitHubAPIError(
                            f"Corrupted logs archive for run {run_id}: {err}"
                        ) from err

                return res.text
            except httpx.HTTPStatusError as err:
                self._handle_error(err, f"Failed to get logs for run {run_id}")
                return ""

    async def get_commit(self, owner: str, repo: str, commit_sha: str) -> dict[str, Any]:
        """Fetches commit metadata from GitHub repository."""
        url = f"{self.base_url}/repos/{owner}/{repo}/commits/{commit_sha}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.get(url, headers=self._get_headers())
                res.raise_for_status()
                return cast(dict[str, Any], res.json())
            except httpx.HTTPStatusError as err:
                self._handle_error(err, f"Failed to get commit {commit_sha}")
                return {}

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        """Creates a Pull Request on target repository."""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.post(url, json=payload, headers=self._get_headers())
                res.raise_for_status()
                logger.info(
                    "github_pull_request_created",
                    repo=f"{owner}/{repo}",
                    head=head,
                    base=base,
                )
                return cast(dict[str, Any], res.json())
            except httpx.HTTPStatusError as err:
                self._handle_error(err, f"Failed to create pull request for {owner}/{repo}")
                return {}

    async def find_pull_request(
        self,
        owner: str,
        repo: str,
        head_branch: str,
        base_branch: str,
    ) -> dict[str, Any] | None:
        """Looks up existing open Pull Request matching head and base branches."""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        params = {
            "state": "open",
            "head": f"{owner}:{head_branch}",
            "base": base_branch,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                res = await client.get(url, params=params, headers=self._get_headers())
                res.raise_for_status()
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    return cast(dict[str, Any], data[0])
                return None
            except httpx.HTTPStatusError as err:
                self._handle_error(err, f"Failed to search pull requests for {owner}/{repo}")
                return None
