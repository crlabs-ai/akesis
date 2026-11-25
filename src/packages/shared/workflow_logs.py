"""Workflow-log evidence for a verified incident (Phase 1.10).

The caller passes a FailureContext whose provenance was verified against persisted state, plus the
VerifiedProvenance token that verification issued. Nothing here reads raw webhook data.

The log archive is untrusted input. It is read entirely in memory (never extracted to disk) under
limits on entries, per-file and total uncompressed size, and compression ratio. Links, devices,
encrypted entries and unsafe paths are rejected. Text is decoded leniently, recognised secret
formats are redacted (a fixed list, not universal secret detection; see the notes below), and
only a bounded excerpt reaches the FailureSignal. Ambiguous logs stay UNKNOWN.
"""

import asyncio
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

# Redaction runs on a whole untrusted log file (bounded by the archive's per-file limit), so every
# step is linear in its input. Python's `re` backtracks; the patterns are safe for these reasons:
# * Each starts at a fixed literal and consumes its token possessively (`++`, `{n,}+`), so a start
#   that fails costs at most the literal plus the minimum length, and one that succeeds consumes
#   the whole token, so later starts inside it are never tried.
# * Two shapes that cannot be written that way are hand-written scanners: private keys (an
#   END must be looked for after each BEGIN) and JWTs (three dot-separated segments). Key=value
#   assignments use a possessive key regex with the keyword check done on the matched key.
#
# Adjacency: a credential is recognised even when glued to other word characters ("f_ghp_...",
# "idAKIA...", "éDB_SECRET=..."). Distinctive prefixes need no boundary at all. Two prefixes that
# also occur inside ordinary words ("sk-" in "task-", "basic" in "nonbasic") must not follow an
# ASCII letter or digit; "_" and Unicode letters still count as a boundary for them.
# Stored signal fields other than the message and the excerpt (paths, functions, code, names).
MAX_FIELD_CHARS = 500
# Group 1 is the key type ("RSA ", "EC ", ...); an END closes only a BEGIN of the same type.
_KEY_BEGIN = re.compile(r"-----BEGIN ([A-Z ]*)PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END ([A-Z ]*)PRIVATE KEY-----")
_TOKEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}+|github_pat_[A-Za-z0-9_]{20,}+)"), REDACTED),
    # The whole [0-9A-Z] run: a key glued to more capitals is redacted with them (over-redaction).
    (re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16,}+"), REDACTED),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}+"), REDACTED),
    (re.compile(r"xox[abposr]-[A-Za-z0-9\-]{10,}+"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}+"), REDACTED),
]
_JWT_RUN = re.compile(r"[A-Za-z0-9_\-.]++")
_CONTEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)(?<![A-Za-z0-9])(bearer|basic)\s++[A-Za-z0-9._~+/=\-]{12,}+"),
        rf"\1 {REDACTED}",
    ),
    (re.compile(r"(://)[^/\s:@]++:[^/\s@]++@"), rf"\1{REDACTED}@"),  # credentials in URLs
]
# The former key and separator grammar without its backtracking. The key starts where a run of
# key characters starts (not at a word boundary), so "éDB_SECRET=" is a key too.
_ASSIGNMENT = re.compile(r"(?i)(?<![A-Z0-9_])([A-Z0-9_]++)\s*+[=:]\s*+")
_SECRET_KEY = re.compile(r"(?i)PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIALS?")
_NON_SPACE = re.compile(r"\S++")


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


def _redact_private_keys(text: str) -> str:
    """Each private-key block, BEGIN through the END marker of the same key type, at any length.

    The END must name the same type as its BEGIN ("RSA ", "EC ", "OPENSSH ", "ENCRYPTED ", or
    none); an END of another type inside the block is part of the block. With no matching END the
    rest of the text is redacted: the block's end cannot be established, so over-redaction is the
    safe failure.

    Linear, one forward pass: a block's END scan starts at its body and stops at its matching END,
    the next BEGIN search starts after that END, and a block with no matching END ends the pass.
    No character is scanned twice.
    """
    parts: list[str] = []
    pos = 0
    while (begin := _KEY_BEGIN.search(text, pos)) is not None:
        parts += (text[pos : begin.start()], REDACTED)
        end = _KEY_END.search(text, begin.end())
        while end is not None and end.group(1) != begin.group(1):
            end = _KEY_END.search(text, end.end())
        if end is None:
            return "".join(parts)
        pos = end.end()
    parts.append(text[pos:])
    return "".join(parts)


def _jwt_start(segment: str) -> int | None:
    """Leftmost `eyJ` in the first segment leaving 11+ characters, glued or not ("f_eyJ...").

    No boundary is needed: the next segment must itself start with `eyJ`, which ordinary text
    practically never does.
    """
    pos = segment.find("eyJ")
    return pos if pos != -1 and len(segment) - pos >= 11 else None


def _redact_jwt_run(match: re.Match[str]) -> str:
    run = match.group()
    if "eyJ" not in run or "." not in run:
        return run
    segments = run.split(".")
    out: list[str] = []
    i = 0
    while i < len(segments):
        if (
            i + 2 < len(segments)
            and segments[i + 1].startswith("eyJ")
            and len(segments[i + 1]) >= 11
            and len(segments[i + 2]) >= 8
            and (start := _jwt_start(segments[i])) is not None
        ):
            out.append(segments[i][:start] + REDACTED)
            i += 3
            continue
        out.append(segments[i])
        i += 1
    return ".".join(out)


def _is_escaped(text: str, pos: int) -> bool:
    """True when an odd number of backslashes immediately precedes `pos`.

    The backslash run cannot extend past the value's opening quote (a quote is not a backslash),
    so the answer does not depend on where a search started.
    """
    before = pos - 1
    while before >= 0 and text[before] == "\\":
        before -= 1
    return (pos - 1 - before) % 2 == 1


def _redact_assignments(text: str) -> str:
    """KEY=value / KEY: value where KEY contains a secret keyword.

    * A quoted value runs to its first unescaped closing quote, across spaces and lines. Escapes
      are honoured for both quote kinds: where a backslash is literal (shell single quotes) the
      value runs on to a later quote, which over-redacts and never exposes.
    * With no closing quote the rest of the text is redacted: the value's end cannot be
      established, so over-redaction is the safe failure.
    * An unquoted value is the next run of non-space characters.
    * Every value is searched for further assignments, so in "SECRET=token: hunter2" the value
      "token:" is a key too and "hunter2" is redacted. Overlapping spans merge. This also makes
      coverage a superset of the former regex's whichever way quotes pair up. A key inside a quoted
      value can therefore extend redaction past the outer closing quote (deliberate over-redaction).

    Linear: each search starts at the previous value's start or later; the next unescaped quote
    of each kind is cached and only moves forward; and every unquoted value inside one non-space
    run ends at that run's end, which is found once and cached.
    """
    spans: list[list[int]] = []
    pos = 0
    run_end = -1  # end of the non-space run holding the latest unquoted value
    next_quote = {'"': -2, "'": -2}  # first unescaped quote at or after the last search; -1 none
    while (match := _ASSIGNMENT.search(text, pos)) is not None:
        value = match.end()
        pos = value
        if value >= len(text) or not _SECRET_KEY.search(match.group(1)):
            continue
        quote = text[value]
        if quote in next_quote:
            close = next_quote[quote]
            if close != -1 and close <= value:
                close = text.find(quote, value + 1)
                while close != -1 and _is_escaped(text, close):
                    close = text.find(quote, close + 1)
                next_quote[quote] = close
            stop = len(text) if close == -1 else close + 1
        else:
            if value >= run_end:
                run_end = _NON_SPACE.match(text, value).end()  # type: ignore[union-attr]
            stop = run_end
        if spans and value <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
        else:
            spans.append([value, stop])
        if stop == len(text):
            break  # everything after is redacted; nothing further can matter
    parts: list[str] = []
    last = 0
    for start, stop in spans:
        parts += (text[last:start], REDACTED)
        last = stop
    parts.append(text[last:])
    return "".join(parts)


def redact(text: str) -> str:
    """Redact every recognised secret format. Linear in len(text); see the notes above."""
    text = _redact_private_keys(text)
    for pattern, replacement in _TOKEN_PATTERNS:
        text = pattern.sub(replacement, text)
    if "eyJ" in text:
        text = _JWT_RUN.sub(_redact_jwt_run, text)
    for pattern, replacement in _CONTEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return _redact_assignments(text)


def redact_tail(text: str, limit: int) -> str:
    """The last `limit` characters of `text`, with no secret in them, whole or cut.

    Every cut in this module goes through this function or `redact_head`, and both rely on two
    steps rather than on a fixed overlap margin:
    1. All of `text` is redacted before the cut. Every secret recognisable anywhere in it is
       already a marker, so the cut can split only a marker or ordinary text. A quoted value or a
       key/value gap of any length within `text` is seen whole.
    2. The cut result is redacted again, so anything the cut made recognisable (a token whose
       preceding character was cut off) is replaced too.

    Linear in len(text). The result can exceed `limit` by at most len(REDACTED) - 1: only a
    match that starts at the cut can be new, and replacing a one-character value grows it by 9.
    """
    return redact(redact(text)[-limit:]) if text else text


def redact_head(text: str, limit: int) -> str:
    """The first `limit` characters of `text`; the same two steps as `redact_tail`."""
    return redact(redact(text)[:limit]) if text else text


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
    # The whole file is redacted before the parse window is cut from it, so a secret that starts
    # before the window cannot leave a fragment inside it. The parser still sees at most
    # `max_parse_chars` (plus at most len(REDACTED) - 1, see `redact_tail`).
    cleaned = redact_tail(_TIMESTAMP.sub("", text), limits.max_parse_chars)
    if not cleaned.strip():
        return None
    signal = filter_and_extract_signal(cleaned)
    job, step = _job_and_step(name)

    # Every stored string is bounded, and redacted again after its cut.
    def field(value: str | None) -> str | None:
        return redact_tail(value, MAX_FIELD_CHARS) if value else value

    return signal.model_copy(
        update={
            "error_type": redact_head(signal.error_type, MAX_FIELD_CHARS),
            "message": redact_head(signal.message, MAX_MESSAGE_CHARS),
            "target_file": field(signal.target_file),
            "extracted_snippet": redact_tail(signal.extracted_snippet, MAX_EXCERPT_CHARS),
            "traceback_frames": [
                frame.model_copy(
                    update={
                        "file_path": field(frame.file_path),
                        "function_name": field(frame.function_name),
                        "code_line": field(frame.code_line),
                    }
                )
                for frame in signal.traceback_frames[:MAX_FRAMES]
            ],
            "job_name": redact_head(job, 255) if job else None,
            "step_name": redact_head(step, 255) if step else None,
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
        # CPU-bound and synchronous: run it off the event loop so the worker stays responsive.
        signal = await asyncio.to_thread(
            lambda: extract_signal(read_log_archive(archive, limits), limits)
        )
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
