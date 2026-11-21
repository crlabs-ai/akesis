"""Workflow-log evidence for a verified incident (Phase 1.10).

The caller passes a FailureContext whose provenance was verified against persisted state, plus the
VerifiedProvenance token that verification issued. Nothing here reads raw webhook data.

The log archive is untrusted input. It is read entirely in memory (never extracted to disk) under
limits on entries, per-file and total uncompressed size, and compression ratio. Links, devices,
encrypted entries and unsafe paths are rejected. Text is decoded leniently, secrets are redacted,
and only a bounded excerpt reaches the FailureSignal. Ambiguous logs stay UNKNOWN.
"""

import io
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.packages.sdk.github_actions_logs import GitHubActionsLogs, LogDownloadError, Resolver
from src.packages.sdk.github_app_auth import GitHubAppAuthenticationError
from src.packages.shared.config import settings
from src.packages.shared.log_parser import filter_and_extract_signal
from src.packages.shared.models import FailureContext, FailureSignal

LOG_TOKEN_PERMISSIONS = {"actions": "read", "metadata": "read"}
MAX_MESSAGE_CHARS = 500
MAX_EXCERPT_CHARS = 4000
MAX_FRAMES = 20
_ERROR_MARKER = "##[error]"
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?", re.MULTILINE)
_STEP_PREFIX = re.compile(r"^\d+_")
REDACTED = "[REDACTED]"
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        REDACTED,
    ),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), REDACTED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}"), rf"\1 {REDACTED}"),
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), rf"\1{REDACTED}@"),  # credentials in URLs
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIALS?)"
            r"[A-Z0-9_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        rf"\1\2{REDACTED}",
    ),
]


class LogArchiveRejected(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LogLimits:
    max_compressed_bytes: int
    max_entries: int
    max_total_bytes: int
    max_file_bytes: int
    max_ratio: int
    max_parse_chars: int
    allowed_hosts: tuple[str, ...]

    @classmethod
    def from_settings(cls) -> "LogLimits":
        return cls(
            max_compressed_bytes=settings.log_archive_max_compressed_bytes,
            max_entries=settings.log_archive_max_entries,
            max_total_bytes=settings.log_archive_max_uncompressed_bytes,
            max_file_bytes=settings.log_archive_max_file_bytes,
            max_ratio=settings.log_archive_max_compression_ratio,
            max_parse_chars=settings.log_parse_max_chars,
            allowed_hosts=tuple(h.lower() for h in settings.log_download_hosts),
        )


@dataclass(frozen=True)
class LogOutcome:
    """extracted (signal set) | retry | unavailable. `code` is a stable, non-sensitive reason."""

    status: str
    code: str | None = None
    signal: FailureSignal | None = None
    headers: dict[str, str] = field(default_factory=dict)


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _safe_name(name: str) -> bool:
    parts = name.rstrip("/").split("/")
    return not (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or (len(parts[0]) >= 2 and parts[0][1] == ":")
        or any(part in ("", ".", "..") for part in parts)
    )


def read_log_archive(data: bytes, limits: LogLimits) -> list[tuple[str, str]]:
    """(entry name, decoded text) for every .txt log in the archive, under strict limits."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise LogArchiveRejected(
            "Log archive is not a valid ZIP", code="malformed_archive"
        ) from None
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise LogArchiveRejected("Too many archive entries", code="archive_too_many_entries")
        declared = 0
        for info in infos:
            mode = info.external_attr >> 16
            if not _safe_name(info.filename):
                raise LogArchiveRejected("Unsafe archive path", code="archive_unsafe_path")
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise LogArchiveRejected(
                    "Unsupported archive entry type", code="archive_entry_type"
                )
            if info.flag_bits & 0x1:
                raise LogArchiveRejected("Encrypted archive entry", code="archive_entry_type")
            if info.file_size > limits.max_file_bytes:
                raise LogArchiveRejected("Archive entry too large", code="archive_too_large")
            if info.file_size and info.file_size > max(info.compress_size, 1) * limits.max_ratio:
                raise LogArchiveRejected("Suspicious compression ratio", code="archive_ratio")
            declared += info.file_size
            if declared > limits.max_total_bytes:
                raise LogArchiveRejected(
                    "Archive too large when expanded", code="archive_too_large"
                )
        entries: list[tuple[str, str]] = []
        actual = 0
        for info in infos:
            if info.is_dir() or not info.filename.endswith(".txt"):
                continue
            try:
                with archive.open(info) as handle:
                    raw = handle.read(limits.max_file_bytes + 1)  # never trust declared sizes
            except (
                zipfile.BadZipFile,
                NotImplementedError,
                RuntimeError,
                zlib.error,
                EOFError,
                OSError,
            ):
                raise LogArchiveRejected(
                    "Corrupt archive entry", code="malformed_archive"
                ) from None
            actual += len(raw)
            if len(raw) > limits.max_file_bytes or actual > limits.max_total_bytes:
                raise LogArchiveRejected(
                    "Archive too large when expanded", code="archive_too_large"
                )
            entries.append(
                (info.filename, raw.decode("utf-8", errors="replace").replace("\x00", ""))
            )
        return entries


def _job_and_step(name: str) -> tuple[str | None, str | None]:
    parts = name.split("/")
    if len(parts) >= 2:
        return parts[0], _STEP_PREFIX.sub("", parts[-1]).removesuffix(".txt")
    return _STEP_PREFIX.sub("", name).removesuffix(".txt"), None


def extract_signal(entries: list[tuple[str, str]], limits: LogLimits) -> FailureSignal | None:
    """The failing step's signal, or None when the archive holds no log text at all."""
    if not entries:
        return None
    failing = [entry for entry in entries if _ERROR_MARKER in entry[1]]
    # Per-step files ("job/1_step.txt") are preferred over the combined per-job copy.
    candidates = [e for e in failing if "/" in e[0]] or failing or entries
    name, text = candidates[0]
    cleaned = redact(_TIMESTAMP.sub("", text))[-limits.max_parse_chars :]
    if not cleaned.strip():
        return None
    signal = filter_and_extract_signal(cleaned)
    job, step = _job_and_step(name)
    return signal.model_copy(
        update={
            "message": redact(signal.message)[:MAX_MESSAGE_CHARS],
            "extracted_snippet": redact(signal.extracted_snippet)[-MAX_EXCERPT_CHARS:],
            "traceback_frames": signal.traceback_frames[:MAX_FRAMES],
            "job_name": job[:255] if job else None,
            "step_name": step[:255] if step else None,
        }
    )


async def retrieve_failure_signal(
    context: FailureContext,
    token: Any,
    *,
    auth: Any,
    api: httpx.AsyncClient,
    limits: LogLimits,
    download_client: Any = None,
    resolver: Resolver | None = None,
) -> LogOutcome:
    """Fetch and parse the verified run attempt's logs. Never raises for expected failures."""
    provenance = context.provenance
    if (
        provenance is None
        or provenance.event_repository.id != token.event_repository_id
        or provenance.source_repository.id != token.source_repository_id
        or provenance.run_id != token.run_id
        or provenance.run_attempt != token.run_attempt
        or provenance.head_sha != token.head_sha
        or context.incident_id != token.incident_id
    ):
        return LogOutcome("unavailable", "provenance_mismatch")
    try:
        installation = await auth.installation_token(
            token.installation_id,
            repository_ids=[token.event_repository_id],
            permissions=dict(LOG_TOKEN_PERMISSIONS),
        )
        logs = GitHubActionsLogs(
            api,
            installation.token,
            full_name=provenance.event_repository.full_name,
            download_client=download_client,
            resolver=resolver,
        )
        await logs.verify_attempt(
            run_id=token.run_id,
            attempt=token.run_attempt,
            head_sha=token.head_sha,
            event_repository_id=token.event_repository_id,
            source_repository_id=token.source_repository_id,
        )
        archive = await logs.download(
            run_id=token.run_id,
            attempt=token.run_attempt,
            max_bytes=limits.max_compressed_bytes,
            allowed_hosts=limits.allowed_hosts,
        )
        signal = extract_signal(read_log_archive(archive, limits), limits)
    except GitHubAppAuthenticationError as error:
        return LogOutcome(
            "retry" if error.retryable else "unavailable", error.code, headers=error.headers
        )
    except LogDownloadError as error:
        return LogOutcome(
            "retry" if error.retryable else "unavailable", error.code, headers=error.headers
        )
    except LogArchiveRejected as error:
        return LogOutcome("unavailable", error.code)
    if signal is None:
        return LogOutcome("unavailable", "logs_empty")
    return LogOutcome("extracted", signal=signal)
