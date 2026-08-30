from typing import Any, Protocol

import httpx

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger

logger = get_logger("akesis.github_client")


class GitHubAPIError(Exception):
    """Base exception for GitHub API communication errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubRateLimitError(GitHubAPIError):
    """Exception raised when GitHub API rate limit is exceeded."""

    pass


class GitHubResourceNotFoundError(GitHubAPIError):
    """Exception raised when requested GitHub run or log is not found."""

    pass


class GitHubClientProtocol(Protocol):
    """Interface for GitHub integration client."""

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]: ...

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str: ...


class GitHubClient:
    """Async HTTP client for communicating with GitHub REST API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.token = token or settings.github_token
        self.base_url = (base_url or settings.github_api_url).rstrip("/")
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Akesis-CI-Triage/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        """Fetches metadata for a specific workflow run."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=self._get_headers())
                if response.status_code == 404:
                    raise GitHubResourceNotFoundError(
                        f"Workflow run {run_id} not found", status_code=404
                    )
                if response.status_code in (403, 429):
                    raise GitHubRateLimitError(
                        "GitHub API rate limit exceeded", status_code=response.status_code
                    )
                response.raise_for_status()
                return response.json()  # type: ignore[no-any-return]
            except httpx.HTTPStatusError as err:
                raise GitHubAPIError(
                    f"GitHub API HTTP error: {err}", status_code=err.response.status_code
                ) from err
            except httpx.RequestError as err:
                raise GitHubAPIError(f"Network error connecting to GitHub: {err}") from err

    async def get_workflow_run_logs(self, owner: str, repo: str, run_id: int) -> str:
        """Fetches and downloads raw logs for a workflow run."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=self._get_headers())
                if response.status_code == 404:
                    raise GitHubResourceNotFoundError(
                        f"Logs for run {run_id} not found", status_code=404
                    )
                if response.status_code in (403, 429):
                    raise GitHubRateLimitError(
                        "GitHub API rate limit exceeded", status_code=response.status_code
                    )
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as err:
                raise GitHubAPIError(
                    f"GitHub API log retrieval error: {err}",
                    status_code=err.response.status_code,
                ) from err
            except httpx.RequestError as err:
                raise GitHubAPIError(f"Network error downloading logs: {err}") from err
