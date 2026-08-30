import re
from pathlib import Path
from typing import NamedTuple

from src.packages.shared.config import settings
from src.packages.shared.context_resolver import (
    is_path_safe_and_within_root,
    normalize_repo_relative_path,
)
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    DiagnosticResult,
    EvidencePackage,
    FilePatch,
    PatchHunk,
)

logger = get_logger("akesis.patch_validator")

HUNK_HEADER_PATTERN = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")

PROTECTED_PATH_PATTERNS = (
    re.compile(r"^\.github/workflows/"),
    re.compile(r"^\.git/"),
    re.compile(r"^\.env(\..+)?$"),
    re.compile(
        r".*\.(exe|dll|so|dylib|bin|pyc|pyd|whl|tar|gz|zip|png|jpg|jpeg|gif|ico)$",
        re.IGNORECASE,
    ),
)

DEPENDENCY_PATH_PATTERNS = (
    re.compile(r"^pyproject\.toml$"),
    re.compile(r"^requirements(-\w+)?\.txt$"),
    re.compile(r"^setup\.(py|cfg)$"),
    re.compile(r"^Pipfile(\.lock)?$"),
    re.compile(r"^poetry\.lock$"),
    re.compile(r"^uv\.lock$"),
)


class PatchValidationResult(NamedTuple):
    """Result of deterministic patch validation."""

    is_valid: bool
    rejection_reasons: list[str]
    file_patches: list[FilePatch]
    target_files: list[str]
    has_dependency_changes: bool
    risk_level: str


class PatchValidator:
    """Performs strict deterministic validation of LLM-generated unified diff patches."""

    def __init__(
        self,
        max_target_files: int | None = None,
        max_patch_lines: int | None = None,
        max_patch_chars: int | None = None,
    ) -> None:
        self.max_target_files = max_target_files or settings.max_fix_target_files
        self.max_patch_lines = max_patch_lines or settings.max_patch_lines
        self.max_patch_chars = max_patch_chars or settings.max_patch_chars

    def validate_patch(
        self,
        raw_diff: str,
        claimed_target_files: list[str],
        evidence_package: EvidencePackage | None = None,
        diagnostic_result: DiagnosticResult | None = None,
        repo_root: Path | None = None,
    ) -> PatchValidationResult:
        """Validates unified diff syntax, path security, grounding, and safety budgets."""
        rejection_reasons: list[str] = []

        if not raw_diff or not raw_diff.strip():
            return PatchValidationResult(
                is_valid=False,
                rejection_reasons=["Patch is empty."],
                file_patches=[],
                target_files=[],
                has_dependency_changes=False,
                risk_level="high",
            )

        # 1. Check budget limits
        if len(raw_diff) > self.max_patch_chars:
            rejection_reasons.append(
                f"Patch char count ({len(raw_diff)}) exceeds maximum ({self.max_patch_chars})."
            )

        lines = raw_diff.splitlines()
        if len(lines) > self.max_patch_lines:
            rejection_reasons.append(
                f"Patch line count ({len(lines)}) exceeds maximum ({self.max_patch_lines})."
            )

        # 2. Parse unified diff into structured FilePatch list
        file_patches, parse_errors = self._parse_unified_diff(raw_diff)
        if parse_errors:
            rejection_reasons.extend(parse_errors)

        if not file_patches and not parse_errors:
            rejection_reasons.append("No valid file diff blocks found in patch.")

        # 3. Check target file count limit
        parsed_paths = [fp.path for fp in file_patches]
        all_target_files = list(
            dict.fromkeys(
                parsed_paths + [normalize_repo_relative_path(p) for p in claimed_target_files if p]
            )
        )

        if len(all_target_files) > self.max_target_files:
            rejection_reasons.append(
                f"Patch modifies {len(all_target_files)} files (max {self.max_target_files})."
            )

        # 4. Compile valid evidence paths for grounding
        grounded_paths: set[str] = set()
        if evidence_package:
            for ev in evidence_package.code_evidences:
                grounded_paths.add(ev.path)
            sig_file = evidence_package.failure_context.signal.target_file
            if sig_file:
                grounded_paths.add(normalize_repo_relative_path(sig_file))
            for frame in evidence_package.failure_context.signal.traceback_frames:
                if frame.file_path:
                    grounded_paths.add(normalize_repo_relative_path(frame.file_path))

        if diagnostic_result and diagnostic_result.proposal.target_file:
            grounded_paths.add(normalize_repo_relative_path(diagnostic_result.proposal.target_file))

        # 5. Validate each target file path
        has_dependency_changes = False

        for file_path in all_target_files:
            norm_path = normalize_repo_relative_path(file_path)

            # Check protected paths
            for pattern in PROTECTED_PATH_PATTERNS:
                if pattern.search(norm_path):
                    rejection_reasons.append(
                        f"Target path '{norm_path}' matches protected security pattern."
                    )

            # Check dependency files
            for dep_pattern in DEPENDENCY_PATH_PATTERNS:
                if dep_pattern.search(norm_path):
                    has_dependency_changes = True

            # Grounding check: Evidence OR verified in repo
            is_grounded = norm_path in grounded_paths
            exists_in_repo = False

            if repo_root is not None:
                safe_path = is_path_safe_and_within_root(repo_root, norm_path)
                if safe_path is None:
                    rejection_reasons.append(
                        f"Target path '{norm_path}' is outside root or uses unsafe traversal."
                    )
                elif safe_path.is_file():
                    exists_in_repo = True
            elif ".." in norm_path or norm_path.startswith("/"):
                rejection_reasons.append(
                    f"Target path '{norm_path}' contains unsafe path traversal elements."
                )

            if not is_grounded and not exists_in_repo:
                rejection_reasons.append(
                    f"Target path '{norm_path}' is neither grounded in evidence "
                    "nor verified to exist in repository."
                )

        # 6. Evaluate Risk Level
        risk_level = "low"
        if has_dependency_changes:
            risk_level = "high"
        elif len(all_target_files) > 1 or len(lines) > 40:
            risk_level = "medium"

        is_valid = len(rejection_reasons) == 0

        logger.info(
            "patch_validation_completed",
            is_valid=is_valid,
            target_files=all_target_files,
            risk_level=risk_level,
            has_dependency_changes=has_dependency_changes,
            rejection_count=len(rejection_reasons),
        )

        return PatchValidationResult(
            is_valid=is_valid,
            rejection_reasons=rejection_reasons,
            file_patches=file_patches,
            target_files=all_target_files,
            has_dependency_changes=has_dependency_changes,
            risk_level=risk_level,
        )

    def _parse_unified_diff(self, raw_diff: str) -> tuple[list[FilePatch], list[str]]:
        """Parses unified diff into structured FilePatch blocks and hunks."""
        file_patches: list[FilePatch] = []
        errors: list[str] = []

        lines = raw_diff.splitlines()
        i = 0
        n = len(lines)

        current_old_path: str | None = None
        current_new_path: str | None = None
        current_target_file: str | None = None
        current_hunks: list[PatchHunk] = []
        current_diff_lines: list[str] = []

        while i < n:
            line = lines[i]

            if line.startswith("--- "):
                if current_target_file and current_hunks:
                    file_patches.append(
                        FilePatch(
                            path=current_target_file,
                            old_path=current_old_path,
                            new_path=current_new_path,
                            hunks=current_hunks,
                            raw_diff="\n".join(current_diff_lines),
                        )
                    )
                    current_hunks = []
                    current_diff_lines = []

                current_old_path = line[4:].strip()
                current_diff_lines = [line]
                i += 1
                if i < n and lines[i].startswith("+++ "):
                    current_new_path = lines[i][4:].strip()
                    current_diff_lines.append(lines[i])

                    raw_target = current_new_path
                    if raw_target.startswith("b/"):
                        raw_target = raw_target[2:]
                    elif current_old_path and current_old_path.startswith("a/"):
                        raw_target = current_old_path[2:]
                    current_target_file = normalize_repo_relative_path(raw_target)
                    i += 1
                else:
                    errors.append(f"Malformed diff: '---' without '+++' at line {i}.")
                continue

            if line.startswith("@@"):
                if not current_target_file:
                    errors.append(f"Hunk header found before file header at line {i + 1}.")
                    i += 1
                    continue

                match = HUNK_HEADER_PATTERN.match(line)
                if not match:
                    errors.append(f"Malformed hunk header syntax at line {i + 1}: {line}")
                    i += 1
                    continue

                old_start = int(match.group(1))
                old_lines = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_lines = int(match.group(4)) if match.group(4) else 1
                header_str = line

                hunk_lines: list[str] = []
                current_diff_lines.append(line)
                i += 1

                while i < n and not lines[i].startswith(("--- ", "@@")):
                    hunk_line = lines[i]
                    if hunk_line.startswith(("+", "-", " ", "\\")):
                        hunk_lines.append(hunk_line)
                        current_diff_lines.append(hunk_line)
                    elif hunk_line == "":
                        hunk_lines.append(" ")
                        current_diff_lines.append(" ")
                    else:
                        errors.append(f"Invalid line prefix in hunk at line {i + 1}: {hunk_line!r}")
                    i += 1

                current_hunks.append(
                    PatchHunk(
                        old_start=old_start,
                        old_lines=old_lines,
                        new_start=new_start,
                        new_lines=new_lines,
                        header=header_str,
                        lines=hunk_lines,
                    )
                )
                continue

            if current_target_file:
                current_diff_lines.append(line)
            i += 1

        if current_target_file and current_hunks:
            file_patches.append(
                FilePatch(
                    path=current_target_file,
                    old_path=current_old_path,
                    new_path=current_new_path,
                    hunks=current_hunks,
                    raw_diff="\n".join(current_diff_lines),
                )
            )

        return file_patches, errors
