"""Verify signed workflow event claims against an installation-scoped GitHub API."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError

from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError
from src.packages.shared.models import RepositoryIdentity, WorkflowRunProvenance


class ProvenanceVerificationError(ValueError):
    """A safe, non-sensitive provenance failure."""

    def __init__(self, message: str, *, code: str = "provenance_mismatch") -> None:
        super().__init__(message)
        self.code = code


class TransientProvenanceError(ProvenanceVerificationError):
    """Verification could not be completed; retry without trusting the event."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provenance_mismatch",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        # Only retry-scheduling headers from the upstream response are kept.
        self.headers: dict[str, str] = dict(headers or {})


def _repository(value: Any) -> RepositoryIdentity:
    if not isinstance(value, dict):
        raise ProvenanceVerificationError("Repository identity is missing")
    full_name = value.get("full_name")
    if (
        not isinstance(full_name, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name)
        or any(part in (".", "..") for part in full_name.split("/"))
    ):
        raise ProvenanceVerificationError("Repository identity is invalid")
    owner, name = full_name.split("/", 1)
    if value.get("name", name) != name:
        raise ProvenanceVerificationError("Repository name is inconsistent")
    if "owner" in value and (
        not isinstance(value["owner"], dict) or value["owner"].get("login") != owner
    ):
        raise ProvenanceVerificationError("Repository owner is inconsistent")
    try:
        return RepositoryIdentity.model_validate({"id": value.get("id"), "full_name": full_name})
    except ValidationError:
        raise ProvenanceVerificationError("Repository identity is invalid") from None


class WorkflowRunProvenanceVerifier:
    def __init__(self, app: GitHubAppAuth, client: httpx.AsyncClient) -> None:
        if str(client.base_url).rstrip("/") != "https://api.github.com":
            raise ValueError("Canonical GitHub API required")
        self._app = app
        self._client = client

    async def verify(self, payload: dict[str, Any], delivery_id: str) -> WorkflowRunProvenance:
        """Call only after webhook HMAC verification; never follow payload URLs."""
        try:
            event_repo = _repository(payload.get("repository"))
            run = payload["workflow_run"]
            source_repo = _repository(run.get("head_repository"))
            installation_id = payload["installation"]["id"]
            run_id, attempt = run["id"], run["run_attempt"]
            sha, branch = run["head_sha"], run["head_branch"]
            if any(type(i) is not int or i <= 0 for i in (installation_id, run_id, attempt)):
                raise ProvenanceVerificationError("Invalid installation or run identity")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ProvenanceVerificationError("Invalid commit SHA")
            if not isinstance(branch, str) or not branch or len(branch) > 255:
                raise ProvenanceVerificationError("Invalid head branch")
            token = await self._app.installation_token(installation_id)
            headers = {
                "Authorization": f"Bearer {token.token}",
                "Accept": "application/vnd.github+json",
            }
            permitted: set[int] = set()
            for page in range(1, 101):
                if token.expires_at <= datetime.now(UTC) + timedelta(seconds=30):
                    raise TransientProvenanceError(
                        "Installation token expired during verification",
                        code="installation_token_expired",
                    )
                response = await self._client.get(
                    "/installation/repositories",
                    params={"per_page": 100, "page": page},
                    headers=headers,
                    follow_redirects=False,
                )
                response.raise_for_status()
                repos = response.json()["repositories"]
                if not isinstance(repos, list):
                    raise ProvenanceVerificationError("Invalid installation repository response")
                permitted.update(_repository(repo).id for repo in repos)
                if len(repos) < 100:
                    break
            else:
                raise ProvenanceVerificationError("Installation repository listing exceeds limit")
            if event_repo.id not in permitted or source_repo.id not in permitted:
                raise ProvenanceVerificationError(
                    "Repository is not accessible to installation", code="repository_inaccessible"
                )
            response = await self._client.get(
                f"/repos/{event_repo.full_name}/actions/runs/{run_id}",
                headers=headers,
                follow_redirects=False,
            )
            response.raise_for_status()
            authoritative = response.json()
            if (
                type(authoritative.get("id")) is not int
                or authoritative.get("id") != run_id
                or type(authoritative.get("run_attempt")) is not int
                or authoritative.get("run_attempt") != attempt
                or _repository(authoritative.get("repository")) != event_repo
                or _repository(authoritative.get("head_repository")) != source_repo
                or authoritative.get("head_sha") != sha
                or authoritative.get("head_branch") != branch
            ):
                raise ProvenanceVerificationError("Workflow run does not match signed event")
            if token.expires_at <= datetime.now(UTC) + timedelta(seconds=30):
                raise TransientProvenanceError(
                    "Installation token expired during verification",
                    code="installation_token_expired",
                )
            response = await self._client.get(
                f"/repos/{source_repo.full_name}", headers=headers, follow_redirects=False
            )
            response.raise_for_status()
            if _repository(response.json()) != source_repo:
                raise ProvenanceVerificationError("Source repository identity changed")
            response = await self._client.get(
                f"/repos/{source_repo.full_name}/commits/{sha}",
                headers=headers,
                follow_redirects=False,
            )
            response.raise_for_status()
            if response.json().get("sha") != sha:
                raise ProvenanceVerificationError("Commit does not match signed event")
            return WorkflowRunProvenance(
                delivery_id=delivery_id,
                event_repository=event_repo,
                source_repository=source_repo,
                head_sha=sha,
                head_branch=branch,
                run_id=run_id,
                run_attempt=attempt,
                installation_id=installation_id,
                verified_at=datetime.now(UTC),
            )
        except ProvenanceVerificationError:
            raise
        except GitHubAppAuthenticationError as error:
            if error.retryable:
                raise TransientProvenanceError(
                    "GitHub App authentication failed", code=error.code, headers=error.headers
                ) from None
            raise ProvenanceVerificationError(
                "GitHub App authentication failed", code=error.code
            ) from None
        except httpx.HTTPStatusError as error:
            response = error.response
            retryable = (
                response.status_code == 429
                or response.status_code >= 500
                or (
                    response.status_code == 403
                    and (
                        "retry-after" in response.headers
                        or response.headers.get("x-ratelimit-remaining") == "0"
                    )
                )
            )
            error_type = TransientProvenanceError if retryable else ProvenanceVerificationError
            code = (
                "github_rate_limited"
                if response.status_code == 429 or (response.status_code == 403 and retryable)
                else "github_api_error"
            )
            if retryable:
                raise TransientProvenanceError(
                    "GitHub verification failed",
                    code=code,
                    headers={
                        name: response.headers[name]
                        for name in ("retry-after", "x-ratelimit-reset")
                        if name in response.headers
                    },
                ) from None
            raise error_type("GitHub verification failed", code=code) from None
        except httpx.RequestError:
            raise TransientProvenanceError(
                "GitHub verification temporarily unavailable", code="github_network_error"
            ) from None
        except (
            KeyError,
            TypeError,
            AttributeError,
            ValueError,
        ):
            raise ProvenanceVerificationError("GitHub provenance verification failed") from None
