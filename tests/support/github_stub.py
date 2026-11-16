"""Transport-level GitHub stub shared by the API and worker processes in end-to-end runs.

GitHub is replaced at the HTTP transport, inside each process. The production verifier runs
unchanged. Behaviour is chosen by installation identifier:

  1001             verification succeeds (a verified delivery and pipeline are created)
  1002             429 with a short Retry-After (retries quickly, then exhausts)
  1003             429 with a long Retry-After (stays retryable)
  1004             429 with a Retry-After beyond the provider ceiling (provider hold)
  any other        429 with the default Retry-After from the environment

Synthetic identifiers only. No request leaves the process.
"""

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable

import httpx

SUCCESS_INSTALLATION = 1001
RUN_HEAD_SHA = "e" * 40
RUN_BRANCH = "feature/e2e"
EVENT_REPOSITORY = {"id": 101, "full_name": "base/repo", "name": "repo", "owner": {"login": "base"}}
SOURCE_REPOSITORY = {"id": 101, "full_name": "base/repo"}
RETRY_AFTER_BY_INSTALLATION = {1002: "1", 1003: "3600", 1004: "90000"}


def _record(log_path: str | None, path: str, method: str, phase: str) -> None:
    if not log_path:
        return
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        line = {
            "pid": os.getpid(),
            "t": time.time(),
            "method": method,
            "path": path,
            "phase": phase,
        }
        os.write(fd, (json.dumps(line) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def make_handler(
    *, log_path: str | None = None, default_retry_after: str = "3600", delay: float = 0.0
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        _record(log_path, path, request.method, "start")
        parts = path.strip("/").split("/")
        if request.method == "GET" and len(parts) == 3 and parts[:2] == ["app", "installations"]:
            installation = int(parts[2])
            if delay:
                await asyncio.sleep(delay)
            if installation == SUCCESS_INSTALLATION:
                return httpx.Response(
                    200, json={"id": installation, "app_id": 123, "suspended_at": None}
                )
            retry_after = RETRY_AFTER_BY_INSTALLATION.get(installation, default_retry_after)
            return httpx.Response(429, headers={"retry-after": retry_after})
        if request.method == "POST" and path.endswith("/access_tokens"):
            return httpx.Response(
                201,
                json={
                    "token": "synthetic-installation-token",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        if request.method == "GET" and path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [EVENT_REPOSITORY]})
        if request.method == "GET" and parts[:4] == ["repos", "base", "repo", "actions"]:
            run_id = int(parts[-1])
            return httpx.Response(
                200,
                json={
                    "id": run_id,
                    "run_attempt": 1,
                    "head_repository": SOURCE_REPOSITORY,
                    "head_sha": RUN_HEAD_SHA,
                    "head_branch": RUN_BRANCH,
                    "conclusion": "failure",
                    "repository": EVENT_REPOSITORY,
                },
            )
        if request.method == "GET" and path == "/repos/base/repo":
            return httpx.Response(200, json=SOURCE_REPOSITORY)
        if request.method == "GET" and path == f"/repos/base/repo/commits/{RUN_HEAD_SHA}":
            return httpx.Response(200, json={"sha": RUN_HEAD_SHA})
        return httpx.Response(404, json={"message": "not stubbed"})

    return handler


class StubbedAsyncClient(httpx.AsyncClient):
    """Replaces httpx.AsyncClient inside a process so the worker and route use the stub."""

    _handler: Callable[[httpx.Request], Awaitable[httpx.Response]] | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        if StubbedAsyncClient._handler is None:
            raise RuntimeError("install_stub() must run before the GitHub client is created")
        handler = StubbedAsyncClient._handler
        kwargs.setdefault("transport", httpx.MockTransport(handler))  # type: ignore[arg-type]  # async handler
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]


def install_stub(handler: Callable[[httpx.Request], Awaitable[httpx.Response]]) -> None:
    StubbedAsyncClient._handler = handler
    httpx.AsyncClient = StubbedAsyncClient  # type: ignore[misc]  # deliberate test-only seam
