"""Download one verified workflow-run attempt's log archive (Phase 1.10).

1. The run attempt is re-read from the canonical API with an installation token and must match the
   verified identity: run ID, attempt, head SHA, event and source repository IDs.
2. The attempt-logs endpoint is requested with redirects disabled. GitHub answers 302 with a signed
   download URL that expires after about a minute.
3. The signed URL must be HTTPS on the default port, carry no credentials, belong to an allowed
   host, and resolve only to public addresses. It is fetched by a separate client that sends no
   Authorization header, ignores proxies and .netrc, follows no further redirect, and streams at
   most ``max_bytes``.

The signed URL and its query string are never logged or placed in an error message.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from src.packages.sdk.github_git_source import API_BASE_URL

Resolver = Callable[[str], Awaitable[list[str]]]
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class LogDownloadError(Exception):
    """Log retrieval failed. Carries a stable code; never a URL, token or response body."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.headers = dict(headers or {})


async def resolve_host(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed)


async def validate_download_url(url: str, allowed: tuple[str, ...], resolver: Resolver) -> None:
    """Refuse anything but HTTPS to an allowed host that resolves only to public addresses."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise LogDownloadError("Unsafe log download URL", code="unsafe_redirect") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not _host_allowed(host, allowed)
    ):
        raise LogDownloadError("Unsafe log download URL", code="unsafe_redirect")
    try:
        addresses = await resolver(host)
    except OSError:
        raise LogDownloadError("Log host did not resolve", code="network", retryable=True) from None
    if not addresses or not all(_is_public(item) for item in addresses):
        # TLS certificate validation for the allowed host is the second barrier against rebinding.
        raise LogDownloadError("Log host resolves to a non-public address", code="unsafe_redirect")


def _is_public(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:  # e.g. scoped IPv6 such as fe80::1%eth0
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not parsed.is_multicast


def _api_error(response: httpx.Response, not_found_code: str) -> LogDownloadError:
    status = response.status_code
    rate_limited = status == 429 or (
        status == 403
        and (
            "retry-after" in response.headers
            or response.headers.get("x-ratelimit-remaining") == "0"
        )
    )
    headers = {
        n: response.headers[n]
        for n in ("retry-after", "x-ratelimit-reset")
        if n in response.headers
    }
    if rate_limited:
        return LogDownloadError(
            "GitHub rate limited", code="github_rate_limited", retryable=True, headers=headers
        )
    if status >= 500:
        return LogDownloadError("GitHub unavailable", code="github_error", retryable=True)
    if status in (404, 410):
        return LogDownloadError("Logs are not available", code=not_found_code)
    if status in (401, 403):
        return LogDownloadError("Installation cannot read workflow logs", code="forbidden")
    return LogDownloadError("Unexpected GitHub response", code="github_error")


class GitHubActionsLogs:
    """Installation-scoped, read-only access to one repository's workflow-run logs."""

    def __init__(
        self,
        api: httpx.AsyncClient,
        token: str,
        *,
        full_name: str,
        download_client: Callable[[], httpx.AsyncClient] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        if str(api.base_url).rstrip("/") != API_BASE_URL:
            raise LogDownloadError("Canonical GitHub API required", code="untrusted_api")
        if not token:
            raise LogDownloadError("Installation token required", code="unauthenticated")
        self._api = api
        self._token = token
        self._full_name = full_name
        self._download_client = download_client or self._default_download_client
        self._resolver = resolver or resolve_host

    @staticmethod
    def _default_download_client() -> httpx.AsyncClient:
        # trust_env=False: no proxy variables and no .netrc credentials are ever applied.
        return httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=False, trust_env=False)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str) -> httpx.Response:
        try:
            return await self._api.get(path, headers=self._headers(), follow_redirects=False)
        except httpx.RequestError:
            raise LogDownloadError("GitHub unavailable", code="network", retryable=True) from None

    async def verify_attempt(
        self,
        *,
        run_id: int,
        attempt: int,
        head_sha: str,
        event_repository_id: int,
        source_repository_id: int,
    ) -> None:
        response = await self._get(
            f"/repos/{self._full_name}/actions/runs/{run_id}/attempts/{attempt}"
        )
        if response.status_code != 200:
            raise _api_error(response, "run_not_found")
        try:
            run = response.json()
            matches = (
                run.get("id") == run_id
                and run.get("run_attempt") == attempt
                and run.get("head_sha") == head_sha
                and (run.get("repository") or {}).get("id") == event_repository_id
                and (run.get("head_repository") or {}).get("id") == source_repository_id
            )
        except (ValueError, AttributeError):
            matches = False
        if not matches:
            raise LogDownloadError(
                "Workflow run does not match verified provenance", code="run_mismatch"
            )

    async def download(
        self, *, run_id: int, attempt: int, max_bytes: int, allowed_hosts: tuple[str, ...]
    ) -> bytes:
        response = await self._get(
            f"/repos/{self._full_name}/actions/runs/{run_id}/attempts/{attempt}/logs"
        )
        if response.status_code not in (301, 302, 303, 307, 308):
            raise _api_error(response, "logs_unavailable")
        location = response.headers.get("location", "")
        await validate_download_url(location, allowed_hosts, self._resolver)
        try:
            async with self._download_client() as client:
                # A fresh request: no Authorization header, no further redirect is followed.
                async with client.stream("GET", location, follow_redirects=False) as download:
                    status = download.status_code
                    if 300 <= status < 400:
                        raise LogDownloadError("Unexpected redirect chain", code="unsafe_redirect")
                    if status in (401, 403, 404, 410):
                        raise LogDownloadError(
                            "Signed log URL expired", code="log_url_expired", retryable=True
                        )
                    if status != 200:
                        raise LogDownloadError(
                            "Log download failed",
                            code="log_download_failed",
                            retryable=status >= 500 or status == 429,
                        )
                    declared = download.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise LogDownloadError(
                            "Log archive too large", code="log_archive_too_large"
                        )
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in download.aiter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise LogDownloadError(
                                "Log archive too large", code="log_archive_too_large"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.TimeoutException:
            raise LogDownloadError(
                "Log download timed out", code="log_download_timeout", retryable=True
            ) from None
        except httpx.RequestError:
            raise LogDownloadError("Log download failed", code="network", retryable=True) from None
