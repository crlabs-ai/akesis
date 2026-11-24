"""Wire protocol between validation callers and the validation executor (Phase 1.14a).

Request body: 4-byte big-endian job length, the job JSON, then the archive: every file's bytes
concatenated in manifest order (each entry carries its size). There is no archive format to parse.

Request MAC (request key):  "akesis-exec-req-v1\\n{method}\\n{path}\\n{timestamp}\\n{sha256(body)}"
Response MAC (response key): "akesis-exec-resp-v1\\n{request signature}\\n{sha256(response body)}"

The response MAC binds each response to the exact request it answers, and the two keys are
separate, so a holder of the request key cannot forge a result. Callers supply no image, argument,
environment, mount, network or resource setting: those are not fields of the job at all.
"""

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.packages.sdk.github_git_source import (
    FileEntry,
    SourceAcquisitionError,
    validate_component,
)
from src.packages.shared.models import ValidationCommand
from src.packages.shared.source_snapshot import _blob_sha, _digest, _tree_sha

VALIDATIONS_PATH = "/v1/validations"
TIMESTAMP_HEADER = "X-Akesis-Timestamp"
SIGNATURE_HEADER = "X-Akesis-Signature"
RESPONSE_SIGNATURE_HEADER = "X-Akesis-Response-Signature"
MAX_JOB_JSON_BYTES = 4_000_000
FILE_MODES = frozenset({"100644", "100755"})

_SHA1 = r"^[0-9a-f]{40}$"
_SHA256 = r"^[0-9a-f]{64}$"
_ID = r"^[A-Za-z0-9_.:-]{1,200}$"


class JobRejected(Exception):
    """The job is refused before any container runs. `code` is a stable, non-sensitive reason."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ManifestEntry(_Strict):
    path: str = Field(min_length=1, max_length=4096)
    mode: Literal["100644", "100755"]
    blob_sha: str = Field(pattern=_SHA1)
    size: int = Field(ge=0)


class ValidationJob(_Strict):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    issued_at: datetime
    expires_at: datetime
    incident_id: str = Field(pattern=_ID)
    pipeline_id: str = Field(pattern=_ID)
    event_repository_id: int = Field(gt=0)
    source_repository_id: int = Field(gt=0)
    commit_sha: str = Field(pattern=_SHA1)
    root_tree_sha: str = Field(pattern=_SHA1)
    snapshot_digest: str = Field(pattern=_SHA256)
    manifest: list[ManifestEntry] = Field(min_length=1, max_length=100_000)
    archive_sha256: str = Field(pattern=_SHA256)
    command: ValidationCommand
    patch: str = Field(max_length=2_000_000)
    patch_sha256: str = Field(pattern=_SHA256)


ResponseStatus = Literal[
    "passed", "failed", "timed_out", "patch_rejected", "infrastructure_error", "rejected"
]


class ExecutorResponse(_Strict):
    job_id: str = Field(max_length=32)
    snapshot_digest: str = Field(max_length=64)
    patch_sha256: str = Field(max_length=64)
    status: ResponseStatus
    reject_code: str | None = Field(default=None, pattern=r"^[a-z_]{1,32}$")
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=20_000)
    stderr: str = Field(default="", max_length=20_000)
    duration_ms: float = 0.0
    timed_out: bool = False


@dataclass(frozen=True)
class JobLimits:
    max_files: int = 2000
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 20_000_000
    max_patch_bytes: int = 200_000


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mac(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def sign_request(key: str, body: bytes, timestamp: int, method: str = "POST") -> str:
    digest = sha256_hex(body)
    return _mac(key, f"akesis-exec-req-v1\n{method}\n{VALIDATIONS_PATH}\n{timestamp}\n{digest}")


def verify_request(
    key: str, body: bytes, timestamp: str, signature: str, *, now: float, skew: float
) -> bool:
    if not re.fullmatch(r"[0-9]{1,12}", timestamp) or abs(now - int(timestamp)) > skew:
        return False
    expected = sign_request(key, body, int(timestamp))
    return hmac.compare_digest(expected, signature)


def sign_response(key: str, request_signature: str, body: bytes) -> str:
    return _mac(key, f"akesis-exec-resp-v1\n{request_signature}\n{sha256_hex(body)}")


def verify_response(key: str, request_signature: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_response(key, request_signature, body), signature)


def encode_request(job_json: bytes, archive: bytes) -> bytes:
    return struct.pack(">I", len(job_json)) + job_json + archive


def decode_request(body: bytes) -> tuple[bytes, bytes]:
    if len(body) < 4:
        raise ValueError("Request body too short")
    (length,) = struct.unpack(">I", body[:4])
    if length > MAX_JOB_JSON_BYTES or 4 + length > len(body):
        raise ValueError("Invalid job length")
    return body[4 : 4 + length], body[4 + length :]


def check_path(path: str) -> None:
    """Repository-relative path of a regular file: same component rules as the snapshot walk."""
    if len(path) > 4096 or path.startswith("/"):
        raise JobRejected("Unsafe path", code="path")
    try:
        for part in path.split("/"):
            validate_component(part)
    except SourceAcquisitionError:
        raise JobRejected("Unsafe path", code="path") from None


def verify_job_contents(job: ValidationJob, archive: bytes, limits: JobLimits) -> dict[str, bytes]:
    """Every file's bytes, after checking them against the manifest, tree and digest."""
    manifest = job.manifest
    if len(manifest) > limits.max_files:
        raise JobRejected("Too many files", code="limits")
    total = sum(entry.size for entry in manifest)
    if total > limits.max_total_bytes or any(e.size > limits.max_file_bytes for e in manifest):
        raise JobRejected("Snapshot exceeds size limits", code="limits")
    if total != len(archive) or sha256_hex(archive) != job.archive_sha256:
        raise JobRejected("Archive does not match its hash", code="integrity")
    paths = [entry.path for entry in manifest]
    folded = {path.casefold() for path in paths}
    if len(set(paths)) != len(paths) or len(folded) != len(paths):
        raise JobRejected("Duplicate or colliding path", code="path")
    for path in paths:
        check_path(path)
        parts = path.split("/")
        for depth in range(1, len(parts)):
            if "/".join(parts[:depth]).casefold() in folded:
                raise JobRejected("A path is both a file and a directory", code="path")
    contents: dict[str, bytes] = {}
    offset = 0
    for entry in manifest:
        data = archive[offset : offset + entry.size]
        offset += entry.size
        if _blob_sha(data) != entry.blob_sha:
            raise JobRejected("File does not match its object ID", code="integrity")
        contents[entry.path] = data
    files: dict[str, tuple[str, str]] = {e.path: (e.mode, e.blob_sha) for e in manifest}
    if _tree_sha(files) != job.root_tree_sha:
        raise JobRejected("Files do not form the stated tree", code="integrity")
    entries = [FileEntry(path=e.path, mode=e.mode, blob_sha=e.blob_sha) for e in manifest]
    if _digest(job.commit_sha, job.root_tree_sha, entries) != job.snapshot_digest:
        raise JobRejected("Snapshot digest does not match", code="integrity")
    return contents


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_MODE_LINE = re.compile(r"^(?:new file mode|deleted file mode|old mode|new mode) (\S+)$")


def _check_patch_path(raw: str) -> None:
    path = raw.split("\t", 1)[0]
    if path == "/dev/null":
        return
    if path.startswith(("a/", "b/")):
        path = path[2:]
    check_path(path)


def verify_patch(patch: str, expected_sha256: str, limits: JobLimits) -> None:
    """Independent of the caller's patch validator: hash, size and every path the patch names."""
    data = patch.encode()
    if len(data) > limits.max_patch_bytes:
        raise JobRejected("Patch too large", code="limits")
    if sha256_hex(data) != expected_sha256:
        raise JobRejected("Patch does not match its hash", code="integrity")
    if "\0" in patch:
        raise JobRejected("Patch contains NUL", code="patch")
    old_left = new_left = 0
    for line in patch.split("\n"):
        if old_left > 0 or new_left > 0:  # hunk body: content, not headers
            if line.startswith(" ") or line == "":  # git apply accepts an empty context line
                old_left, new_left = old_left - 1, new_left - 1
            elif line.startswith("-"):
                old_left -= 1
            elif line.startswith("+"):
                new_left -= 1
            elif not line.startswith("\\"):
                raise JobRejected("Malformed hunk", code="patch")
            continue
        if hunk := _HUNK.match(line):
            old_left = int(hunk.group(1) or "1")
            new_left = int(hunk.group(2) or "1")
        elif line.startswith(("--- ", "+++ ")):
            _check_patch_path(line[4:])
        elif line.startswith("diff --git "):
            for token in line[len("diff --git ") :].split(" "):
                _check_patch_path(token)
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            check_path(line.split(" ", 2)[2])
        elif line.startswith("GIT binary patch") or line.startswith("Binary files "):
            raise JobRejected("Binary patches are not accepted", code="patch")
        elif mode := _MODE_LINE.match(line):
            if mode.group(1) not in FILE_MODES:
                raise JobRejected("Unsupported file mode in patch", code="patch")
