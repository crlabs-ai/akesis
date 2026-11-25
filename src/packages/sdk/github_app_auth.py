"""GitHub App authentication, separate from repository provenance verification."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import jwt


class GitHubAppAuthenticationError(Exception):
    """App or installation authentication failed without exposing credentials."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "app_authentication_failed",
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        # Only retry-scheduling headers from the upstream response are kept.
        self.headers: dict[str, str] = dict(headers or {})


@dataclass(frozen=True)
class InstallationToken:
    token: str = field(repr=False)
    expires_at: datetime
    installation_id: int


class GitHubAppAuth:
    """Verify an installation against the App and issue uncached installation tokens."""

    def __init__(
        self,
        app_id: int,
        private_key: str,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        if app_id <= 0 or not private_key.strip():
            raise ValueError("GitHub App ID and private key are required")
        if str(client.base_url).rstrip("/") != "https://api.github.com":
            raise ValueError("GitHub App authentication requires the canonical GitHub API")
        self._app_id = app_id
        self._private_key = private_key
        self._client = client

    def _app_jwt(self) -> str:
        now = int(datetime.now(UTC).timestamp())
        try:
            return jwt.encode(
                {"iss": str(self._app_id), "iat": now - 60, "exp": now + 540},
                self._private_key,
                algorithm="RS256",
            )
        except Exception:
            raise GitHubAppAuthenticationError("Unable to sign GitHub App JWT") from None

    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> InstallationToken:
        """Verify App ownership for every issuance; never cache credentials.

        `repository_ids` and `permissions` narrow the token. When GitHub reports the granted
        repositories or permissions, any grant beyond the request fails closed.
        """
        if isinstance(installation_id, bool) or installation_id <= 0:
            raise GitHubAppAuthenticationError("Invalid installation ID")
        scope: dict[str, object] = {}
        if repository_ids is not None:
            if not repository_ids or any(
                isinstance(item, bool) or not isinstance(item, int) or item <= 0
                for item in repository_ids
            ):
                raise GitHubAppAuthenticationError("Invalid repository scope")
            scope["repository_ids"] = list(repository_ids)
        if permissions is not None:
            if not permissions:
                raise GitHubAppAuthenticationError("Invalid permission scope")
            scope["permissions"] = dict(permissions)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._app_jwt()}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = await self._client.get(
                f"/app/installations/{installation_id}", headers=headers, follow_redirects=False
            )
            response.raise_for_status()
            installation = response.json()
            if installation.get("suspended_at") is not None:
                raise GitHubAppAuthenticationError(
                    "GitHub App installation suspended", code="installation_suspended"
                )
            if (
                installation.get("id") != installation_id
                or installation.get("app_id") != self._app_id
            ):
                raise GitHubAppAuthenticationError("GitHub App installation mismatch")
            response = await self._client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers=headers,
                json=scope or None,
                follow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
            if repository_ids is not None:
                granted = data.get("repositories")
                if data.get("repository_selection") not in (None, "selected") or (
                    granted is not None
                    and {item.get("id") for item in granted} != set(repository_ids)
                ):
                    raise GitHubAppAuthenticationError(
                        "Installation token exceeds the requested repositories",
                        code="token_scope_mismatch",
                    )
            if permissions is not None:
                granted_permissions = data.get("permissions")
                if granted_permissions is not None and not set(
                    granted_permissions.items()
                ).issubset(permissions.items()):
                    raise GitHubAppAuthenticationError(
                        "Installation token exceeds the requested permissions",
                        code="token_scope_mismatch",
                    )
            token = data["token"]
            expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            if (
                not isinstance(token, str)
                or not token
                or expires_at.tzinfo is None
                or expires_at <= datetime.now(UTC) + timedelta(seconds=60)
            ):
                raise GitHubAppAuthenticationError(
                    "Invalid installation token response",
                    code="invalid_installation_token",
                    retryable=True,
                )
            return InstallationToken(token, expires_at, installation_id)
        except GitHubAppAuthenticationError:
            raise
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
            raise GitHubAppAuthenticationError(
                "GitHub App authentication failed",
                code="github_rate_limited"
                if response.status_code == 429 or (response.status_code == 403 and retryable)
                else "github_app_http_error",
                retryable=retryable,
                headers={
                    name: response.headers[name]
                    for name in ("retry-after", "x-ratelimit-reset")
                    if name in response.headers
                },
            ) from None
        except httpx.RequestError:
            raise GitHubAppAuthenticationError(
                "GitHub App authentication unavailable", code="github_network_error", retryable=True
            ) from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise GitHubAppAuthenticationError("GitHub App authentication failed") from None
