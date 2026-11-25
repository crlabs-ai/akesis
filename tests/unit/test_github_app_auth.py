from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError


@pytest.fixture
def keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


@pytest.mark.asyncio
async def test_verified_installation_uses_short_lived_signed_jwt(keys: tuple[str, str]) -> None:
    private, public = keys
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        claims = jwt.decode(
            request.headers["Authorization"].removeprefix("Bearer "),
            public,
            algorithms=["RS256"],
        )
        now = int(datetime.now(UTC).timestamp())
        assert claims["iss"] == "123"
        assert now - 65 <= claims["iat"] <= now - 60
        assert now < claims["exp"] <= now + 540
        if request.method == "GET":
            return httpx.Response(200, json={"id": 456, "app_id": 123, "suspended_at": None})
        return httpx.Response(
            201,
            json={
                "token": "synthetic-token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        auth = GitHubAppAuth(123, private, client=client)
        first = await auth.installation_token(456)
        await auth.installation_token(456)
    assert "synthetic-token" not in repr(first)
    assert len(calls) == 4  # A fresh verification and token on each request.


@pytest.mark.asyncio
async def test_wrong_app_cannot_mint_token(keys: tuple[str, str]) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"id": 456, "app_id": 999, "suspended_at": None})

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError):
            await GitHubAppAuth(123, keys[0], client=client).installation_token(456)
    assert calls == ["GET"]


@pytest.mark.asyncio
async def test_expired_installation_token_rejected(keys: tuple[str, str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": 456, "app_id": 123, "suspended_at": None})
        return httpx.Response(
            201, json={"token": "synthetic-token", "expires_at": "2000-01-01T00:00:00Z"}
        )

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError):
            await GitHubAppAuth(123, keys[0], client=client).installation_token(456)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "installation",
    [
        {"id": 789, "app_id": 123, "suspended_at": None},
        {"id": 456, "app_id": 123, "suspended_at": "2026-01-01T00:00:00Z"},
    ],
)
async def test_invalid_installation_is_not_issued_a_token(
    keys: tuple[str, str], installation: dict[str, object]
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=installation)

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError):
            await GitHubAppAuth(123, keys[0], client=client).installation_token(456)
    assert calls == ["GET"]


@pytest.mark.asyncio
async def test_noncanonical_api_is_rejected(keys: tuple[str, str]) -> None:
    async with httpx.AsyncClient(base_url="https://example.invalid") as client:
        with pytest.raises(ValueError, match="canonical"):
            GitHubAppAuth(123, keys[0], client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "retryable", "code"),
    [
        (401, {}, False, "github_app_http_error"),
        (403, {}, False, "github_app_http_error"),
        (403, {"x-ratelimit-remaining": "0"}, True, "github_rate_limited"),
        (429, {"retry-after": "5"}, True, "github_rate_limited"),
        (503, {}, True, "github_app_http_error"),
    ],
)
async def test_app_http_failure_classification(
    keys: tuple[str, str], status: int, headers: dict[str, str], retryable: bool, code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, text="synthetic secret response")

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError) as exc:
            await GitHubAppAuth(123, keys[0], client=client).installation_token(456)
    assert exc.value.retryable is retryable and exc.value.code == code
    assert "synthetic secret response" not in str(exc.value)


def _scoped_handler(token_response: dict[str, object], bodies: list[bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": 456, "app_id": 123, "suspended_at": None})
        bodies.append(request.content)
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return httpx.Response(201, json={"token": "t", "expires_at": expires, **token_response})

    return httpx.MockTransport(handler)


SCOPE = {"contents": "write", "pull_requests": "write", "metadata": "read"}


@pytest.mark.asyncio
async def test_scoped_token_requests_only_the_named_repository_and_permissions(
    keys: tuple[str, str],
) -> None:
    import json

    bodies: list[bytes] = []
    granted = {"repository_selection": "selected", "repositories": [{"id": 1001}]}
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=_scoped_handler({**granted, "permissions": SCOPE}, bodies),
    ) as client:
        auth = GitHubAppAuth(123, keys[0], client=client)
        await auth.installation_token(456, repository_ids=[1001], permissions=dict(SCOPE))
        await auth.installation_token(456)  # unscoped callers keep sending no body
    assert json.loads(bodies[0]) == {"repository_ids": [1001], "permissions": SCOPE}
    assert bodies[1] == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grant",
    [
        {"repository_selection": "all"},
        {"repositories": [{"id": 1001}, {"id": 2002}]},
        {"permissions": {**SCOPE, "administration": "write"}},
        {"permissions": {**SCOPE, "metadata": "write"}},
    ],
)
async def test_token_broader_than_requested_is_refused(
    keys: tuple[str, str], grant: dict[str, object]
) -> None:
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=_scoped_handler(grant, [])
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError) as error:
            await GitHubAppAuth(123, keys[0], client=client).installation_token(
                456, repository_ids=[1001], permissions=dict(SCOPE)
            )
    assert error.value.code == "token_scope_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_ids", "permissions"), [([], None), ([0], None), ([True], None), (None, {})]
)
async def test_invalid_scope_request_is_refused_before_any_call(
    keys: tuple[str, str], repository_ids: list[int] | None, permissions: dict[str, str] | None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent for an invalid scope")

    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(GitHubAppAuthenticationError):
            await GitHubAppAuth(123, keys[0], client=client).installation_token(
                456, repository_ids=repository_ids, permissions=permissions
            )
