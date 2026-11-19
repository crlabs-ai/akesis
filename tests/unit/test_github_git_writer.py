"""Guards of the installation-scoped Git Data writer not reached by the mutation tests."""

from typing import Any

import httpx
import pytest

from src.packages.sdk.github_git_writer import GitHubRepositoryWriter, GitHubWriteError

TOKEN = "ghs_writer_test_token"  # noqa: S105 - synthetic test credential
FULL = "crlabs-ai/akesis"


def _writer(handler: Any, base_url: str = "https://api.github.com") -> GitHubRepositoryWriter:
    client = httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))
    return GitHubRepositoryWriter(client, TOKEN, repository_id=1001, full_name=FULL)


def test_only_the_canonical_api_and_a_valid_identity_are_accepted() -> None:
    with pytest.raises(GitHubWriteError):
        _writer(lambda r: httpx.Response(200), base_url="https://evil.example")
    client = httpx.AsyncClient(base_url="https://api.github.com")
    for kwargs in ({"full_name": "../etc"}, {"full_name": "a/b/c"}, {"repository_id": 0}):
        options: dict[str, Any] = {"repository_id": 1001, "full_name": FULL, **kwargs}
        with pytest.raises(GitHubWriteError):
            GitHubRepositoryWriter(client, TOKEN, **options)
    with pytest.raises(GitHubWriteError):
        GitHubRepositoryWriter(client, "", repository_id=1001, full_name=FULL)


@pytest.mark.asyncio
async def test_branch_head_is_exact_and_missing_branch_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith("/heads/missing"):
            return httpx.Response(404, json={})
        # A prefix match must not be accepted as the requested branch.
        return httpx.Response(
            200,
            json={"ref": "refs/heads/main-old", "object": {"type": "commit", "sha": "a" * 40}},
        )

    writer = _writer(handler)
    assert await writer.branch_head("missing") is None
    with pytest.raises(GitHubWriteError):
        await writer.branch_head("main")


@pytest.mark.asyncio
async def test_existing_branch_is_reported_never_forced() -> None:
    seen: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.content))
        return httpx.Response(422, json={"message": "Reference already exists"})

    assert await _writer(handler).create_branch("akesis/fix/x", "a" * 40) is False
    assert [method for method, _ in seen] == ["POST"]
    assert b"force" not in seen[0][1]


@pytest.mark.asyncio
async def test_errors_carry_a_code_and_never_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": TOKEN})

    with pytest.raises(GitHubWriteError) as error:
        await _writer(handler).create_blob(b"x")
    assert error.value.code == "github_error"
    assert TOKEN not in str(error.value)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(GitHubWriteError) as network:
        await _writer(offline).get_repository()
    assert network.value.code == "network"


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["../../pulls", "/main", "main/", "a\\b", ""])
async def test_branch_names_that_could_escape_the_repository_path_are_refused(branch: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request for an invalid branch name")

    writer = _writer(handler)
    with pytest.raises(GitHubWriteError):
        await writer.branch_head(branch)
    with pytest.raises(GitHubWriteError):
        await writer.create_branch(branch, "a" * 40)
