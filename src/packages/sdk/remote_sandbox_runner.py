"""Caller side of the validation executor (Phase 1.14a).

`RemoteSandboxRunner` implements `SandboxRunnerProtocol`. It never runs Docker and never falls back
to local Docker: every transport failure, rejection, bad signature or mismatched echo raises
`DockerUnavailableError`, which `ValidationService` records as `INFRASTRUCTURE_ERROR` (fail closed).
Results map back to the exceptions the local runner raises, so a `ValidationResult` is identical.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from src.packages.sdk.sandbox_runner import (
    DockerSandboxRunner,
    DockerUnavailableError,
    PatchApplicationError,
    SandboxRunnerProtocol,
    SandboxTimeoutError,
    ValidationExecutionResult,
)
from src.packages.shared.config import settings
from src.packages.shared.executor_protocol import (
    RESPONSE_SIGNATURE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    VALIDATIONS_PATH,
    ExecutorResponse,
    ManifestEntry,
    ValidationJob,
    encode_request,
    sha256_hex,
    sign_request,
    verify_response,
)
from src.packages.shared.logging import get_logger
from src.packages.shared.models import ValidationCommand
from src.packages.shared.provenance import require_verified_provenance
from src.packages.shared.source_snapshot import export_snapshot, require_snapshot_bound

logger = get_logger("akesis.remote_sandbox_runner")

JOB_LIFETIME_SECONDS = 300


class ExecutorUnavailableError(DockerUnavailableError):
    """The executor could not be reached, or its response could not be trusted."""


class ExecutorRejectedError(DockerUnavailableError):
    """The executor refused the job (signed `rejected`). `code` is its reason."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Validation executor rejected the job ({code})")
        self.code = code


def default_sandbox_runner() -> SandboxRunnerProtocol:
    """Remote executor when configured, otherwise the local Docker runner (transitional)."""
    if settings.sandbox_executor_url:
        return RemoteSandboxRunner()
    return DockerSandboxRunner()


class RemoteSandboxRunner:
    def __init__(
        self,
        url: str | None = None,
        request_key: str | None = None,
        response_key: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = url or settings.sandbox_executor_url
        self._request_key = request_key or settings.sandbox_executor_request_key
        self._response_key = response_key or settings.sandbox_executor_response_key
        self._timeout = timeout_seconds or settings.sandbox_executor_timeout_seconds
        self._transport = transport or httpx.AsyncHTTPTransport(uds=url.removeprefix("unix://"))

    async def run_validation(
        self,
        snapshot: object,
        patch_diff: str,
        command: ValidationCommand,
        timeout_seconds: float | None = None,
        *,
        provenance: object | None = None,
    ) -> ValidationExecutionResult:
        """Same gates as the local runner: a snapshot-bound provenance token, then the hold."""
        require_snapshot_bound(provenance, snapshot)
        require_verified_provenance()
        return await self._run_validation_unchecked(snapshot, patch_diff, command)

    async def _run_validation_unchecked(
        self, snapshot: object, patch_diff: str, command: ValidationCommand
    ) -> ValidationExecutionResult:
        """Body. Private: callers must use the gated public method. Timeouts are executor config."""
        handle, manifest, contents = export_snapshot(snapshot)
        archive = b"".join(contents)
        now = datetime.now(UTC)
        job = ValidationJob(
            job_id=uuid.uuid4().hex,
            issued_at=now,
            expires_at=now + timedelta(seconds=JOB_LIFETIME_SECONDS),
            incident_id=handle.incident_id,
            pipeline_id=handle.pipeline_id,
            event_repository_id=handle.event_repository_id,
            source_repository_id=handle.source_repository_id,
            commit_sha=handle.commit_sha,
            root_tree_sha=handle.root_tree_sha,
            snapshot_digest=handle.digest,
            manifest=[
                ManifestEntry(path=e.path, mode=e.mode, blob_sha=e.blob_sha, size=len(data))  # type: ignore[arg-type]
                for e, data in zip(manifest, contents, strict=True)
            ],
            archive_sha256=sha256_hex(archive),
            command=command,
            patch=patch_diff or "",
            patch_sha256=sha256_hex((patch_diff or "").encode()),
        )
        body = encode_request(job.model_dump_json().encode(), archive)
        timestamp = int(time.time())
        signature = sign_request(self._request_key, body, timestamp)
        try:
            async with httpx.AsyncClient(
                transport=self._transport, base_url="http://executor", timeout=self._timeout
            ) as client:
                response = await client.post(
                    VALIDATIONS_PATH,
                    content=body,
                    headers={
                        TIMESTAMP_HEADER: str(timestamp),
                        SIGNATURE_HEADER: signature,
                        "Content-Type": "application/octet-stream",
                    },
                )
        except httpx.HTTPError as error:
            logger.warning("validation_executor_unreachable", error_type=type(error).__name__)
            raise ExecutorUnavailableError("Validation executor unavailable") from None
        return self._result(job, signature, response)

    def _result(
        self, job: ValidationJob, signature: str, response: httpx.Response
    ) -> ValidationExecutionResult:
        received = response.headers.get(RESPONSE_SIGNATURE_HEADER, "")
        if not verify_response(self._response_key, signature, response.content, received):
            logger.error("validation_executor_response_unsigned", job_id=job.job_id)
            raise ExecutorUnavailableError("Validation executor response is not authentic")
        try:
            result = ExecutorResponse.model_validate_json(response.content)
        except ValidationError:
            raise ExecutorUnavailableError("Validation executor response is malformed") from None
        if result.status == "rejected":
            raise ExecutorRejectedError(result.reject_code or "unknown")
        if (result.job_id, result.snapshot_digest, result.patch_sha256) != (
            job.job_id,
            job.snapshot_digest,
            job.patch_sha256,
        ):
            logger.error("validation_executor_response_mismatch", job_id=job.job_id)
            raise ExecutorUnavailableError("Validation executor response is for another job")
        if result.status == "patch_rejected":
            raise PatchApplicationError(result.stderr)
        if result.status == "timed_out":
            raise SandboxTimeoutError(result.stderr)
        if result.status == "infrastructure_error":
            raise DockerUnavailableError(result.stderr)
        if (result.status == "passed") != (result.exit_code == 0):
            raise ExecutorUnavailableError("Validation executor response is inconsistent")
        return ValidationExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            timed_out=False,
        )
