import re
from pathlib import Path

from src.packages.sdk.repo_checkout import (
    GitRepositoryCheckoutManager,
    InvalidCommitError,
    RepoCheckoutProtocol,
    RepositoryCheckoutError,
)
from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    CodeEvidence,
    EvidencePackage,
    FailureContext,
)

logger = get_logger("akesis.context_resolver")

COMMON_RUNNER_PREFIXES = (
    "/home/runner/work/",
    "/workspace/",
    "/app/",
    "/runner/",
    "/root/",
)

LANGUAGE_EXTENSION_MAP = {
    ".py": "python",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "bash",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
}


def normalize_repo_relative_path(raw_path: str, repo_name: str = "") -> str:
    """Cleans runner prefixes and normalizes path to repository-relative format."""
    normalized = raw_path.replace("\\", "/").strip()

    for prefix in COMMON_RUNNER_PREFIXES:
        if prefix in normalized:
            idx = normalized.find(prefix) + len(prefix)
            sub = normalized[idx:]
            # If repo_name is repeated in runner path (e.g. repo/repo/src/...)
            if repo_name and sub.startswith(f"{repo_name}/"):
                sub = sub[len(repo_name) + 1 :]
            if "/" in sub:
                # Discard first repo container folder if present
                parts = sub.split("/", 1)
                if len(parts) == 2 and not parts[0].endswith((".py", ".json", ".toml", ".yml")):
                    sub = parts[1]
            normalized = sub
            break

    # Strip leading slashes
    return normalized.lstrip("/")


def is_path_safe_and_within_root(repo_root: Path, target_rel_path: str) -> Path | None:
    """Validates path does not traverse outside repository root."""
    if not target_rel_path or ".." in target_rel_path or target_rel_path.startswith("/"):
        return None

    try:
        resolved_root = repo_root.resolve()
        candidate = (repo_root / target_rel_path).resolve()
        if candidate.is_relative_to(resolved_root):
            return candidate
        return None
    except Exception:
        return None


class CodebaseContextResolver:
    """Resolves relevant source files and extracts bounded context snippets."""

    def __init__(
        self,
        checkout_manager: RepoCheckoutProtocol | None = None,
        max_window_lines: int | None = None,
        max_file_size_bytes: int | None = None,
        max_evidence_files: int | None = None,
        max_total_chars: int | None = None,
    ) -> None:
        self.checkout_manager = checkout_manager or GitRepositoryCheckoutManager()
        self.max_window_lines = max_window_lines or settings.max_context_window_lines
        self.max_file_size = max_file_size_bytes or settings.max_file_size_bytes
        self.max_evidence_files = max_evidence_files or settings.max_evidence_files
        self.max_total_chars = max_total_chars or settings.max_total_source_chars

    def resolve_context(
        self,
        failure_context: FailureContext,
        repo_root: Path | None = None,
    ) -> EvidencePackage:
        """Extracts bounded source code snippets around failure locations."""
        incident_id = failure_context.incident_id
        commit_sha = failure_context.commit_sha
        repo_owner = failure_context.repository_owner
        repo_name = failure_context.repository_name

        logger.info(
            "codebase_context_started",
            incident_id=incident_id,
            repo=f"{repo_owner}/{repo_name}",
            commit_sha=commit_sha,
        )

        notes: list[str] = []
        evidences: list[CodeEvidence] = []

        # 1. Obtain verified repository root
        if repo_root is None:
            try:
                repo_root = self.checkout_manager.checkout_commit(
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    commit_sha=commit_sha,
                )
                notes.append(f"Successfully checked out {repo_owner}/{repo_name}@{commit_sha[:8]}")
            except (InvalidCommitError, RepositoryCheckoutError) as err:
                logger.warning(
                    "codebase_context_unavailable",
                    incident_id=incident_id,
                    reason=str(err),
                )
                return EvidencePackage(
                    incident_id=incident_id,
                    commit_sha=commit_sha,
                    failure_context=failure_context,
                    code_evidences=[],
                    retrieval_status="unavailable",
                    retrieval_notes=[f"Repository checkout failed: {err}"],
                )

        # 2. Discover relevant candidate target files
        candidate_targets: list[tuple[str, int | None]] = []

        signal = failure_context.signal
        if signal.target_file:
            candidate_targets.append((signal.target_file, signal.target_line))

        for frame in signal.traceback_frames:
            if frame.file_path and (frame.file_path, frame.line_number) not in candidate_targets:
                candidate_targets.append((frame.file_path, frame.line_number))

        if not candidate_targets:
            logger.info("no_relevant_files_discovered", incident_id=incident_id)
            return EvidencePackage(
                incident_id=incident_id,
                commit_sha=commit_sha,
                failure_context=failure_context,
                code_evidences=[],
                retrieval_status="empty",
                retrieval_notes=notes + ["No target source file identified from failure signal."],
            )

        # 3. Read and extract bounded context snippets
        total_chars_accumulated = 0

        for raw_path, target_line in candidate_targets:
            if len(evidences) >= self.max_evidence_files:
                notes.append(f"Reached maximum file limit ({self.max_evidence_files})")
                break

            clean_rel_path = normalize_repo_relative_path(raw_path, repo_name)
            safe_path = is_path_safe_and_within_root(repo_root, clean_rel_path)

            if not safe_path:
                logger.warning(
                    "rejected_unsafe_or_traversal_path",
                    raw_path=raw_path,
                    cleaned=clean_rel_path,
                )
                notes.append(f"Rejected unsafe or traversing path: {raw_path}")
                continue

            if not safe_path.is_file():
                logger.info("file_not_found_in_repository", path=clean_rel_path)
                notes.append(f"File not found at commit {commit_sha[:8]}: {clean_rel_path}")
                continue

            try:
                file_size = safe_path.stat().st_size
                if file_size > self.max_file_size:
                    notes.append(f"Skipped {clean_rel_path}: size {file_size}B exceeds limit")
                    continue

                with open(safe_path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                total_lines = len(lines)
                if total_lines == 0:
                    notes.append(f"Skipped empty file: {clean_rel_path}")
                    continue

                # Compute bounded line range
                half_window = self.max_window_lines // 2
                if target_line is not None and 1 <= target_line <= total_lines:
                    start_line = max(1, target_line - half_window)
                    end_line = min(total_lines, target_line + half_window)
                else:
                    start_line = 1
                    end_line = min(total_lines, self.max_window_lines)

                # Format numbered lines
                snippet_lines = []
                for idx in range(start_line, end_line + 1):
                    line_text = lines[idx - 1].rstrip("\r\n")
                    marker = " >" if target_line == idx else "  "
                    snippet_lines.append(f"{idx:4d}{marker} | {line_text}")

                snippet_content = "\n".join(snippet_lines)

                # Enforce char budget
                if total_chars_accumulated + len(snippet_content) > self.max_total_chars:
                    notes.append(f"Snippet for {clean_rel_path} exceeded remaining char budget")
                    break

                ext = safe_path.suffix.lower()
                lang = LANGUAGE_EXTENSION_MAP.get(ext, "text")

                evidence = CodeEvidence(
                    path=clean_rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    target_line=target_line,
                    content=snippet_content,
                    total_file_lines=total_lines,
                    language=lang,
                )
                evidences.append(evidence)
                total_chars_accumulated += len(snippet_content)
                notes.append(f"Extracted {clean_rel_path} (lines {start_line}-{end_line})")

                logger.info(
                    "source_context_extracted",
                    path=clean_rel_path,
                    lines=f"{start_line}-{end_line}",
                    target_line=target_line,
                )

                # Recursively discover imported local Python modules as candidate context
                if ext == ".py":
                    for raw_l in lines:
                        stripped = raw_l.strip()
                        if stripped.startswith("from ") or stripped.startswith("import "):
                            for pattern in (
                                r"^from\s+([a-zA-Z0-9_\.]+)\s+import",
                                r"^import\s+([a-zA-Z0-9_\.]+)",
                            ):
                                match = re.match(pattern, stripped)
                                if match:
                                    mod = match.group(1)
                                    potential_rel = mod.replace(".", "/") + ".py"
                                    if not any(c[0] == potential_rel for c in candidate_targets):
                                        pot_safe = is_path_safe_and_within_root(
                                            repo_root, potential_rel
                                        )
                                        if pot_safe and pot_safe.is_file():
                                            candidate_targets.append((potential_rel, None))

            except Exception as err:
                logger.error("file_read_error", path=clean_rel_path, error=str(err))
                notes.append(f"Error reading {clean_rel_path}: {err}")

        status: str = "success" if evidences else "empty"
        if evidences and len(evidences) < len(candidate_targets):
            status = "partial"

        return EvidencePackage(
            incident_id=incident_id,
            commit_sha=commit_sha,
            failure_context=failure_context,
            code_evidences=evidences,
            retrieval_status=status,  # type: ignore[arg-type]
            retrieval_notes=notes,
        )
