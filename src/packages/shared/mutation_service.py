import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import git

from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
    MutationRepository,
    MutationRepositoryProtocol,
)
from src.packages.database.session import get_session_factory
from src.packages.sdk.github_client import (
    GitHubClient,
    GitHubClientProtocol,
)
from src.packages.sdk.repo_checkout import (
    GitRepositoryCheckoutManager,
    RepoCheckoutProtocol,
)
from src.packages.sdk.sandbox_runner import (
    DockerSandboxRunner,
    SandboxRunnerProtocol,
)
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    ApprovalRecord,
    ApprovalStatus,
    FailureContext,
    FixProposal,
    MutationRecord,
    MutationStatus,
    ValidationResult,
    ValidationStatus,
)
from src.packages.shared.validation_service import ValidationService

logger = get_logger("akesis.mutation_service")

# Regex for safe branch names
SAFE_BRANCH_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\./]+$")
PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "release", "production", "staging"})


class MutationError(Exception):
    """Base exception for Git mutation and PR creation failures."""

    pass


class PreflightCheckError(MutationError):
    """Raised when preconditions, approval bindings, or validation checks fail."""

    pass


class StaleCommitError(MutationError):
    """Raised when repository commit SHA does not match approved proposal SHA."""

    pass


class UnsafeBranchError(MutationError):
    """Raised when branch name violates safety constraints or targets protected branch."""

    pass


class WorkingTreeMismatchError(MutationError):
    """Raised when patch creates unexpected file changes outside approved target scope."""

    pass


class PrePushValidationError(MutationError):
    """Raised when post-patch sandbox validation fails prior to push."""

    pass


class GitPushError(MutationError):
    """Raised when git push operation fails."""

    pass


@asynccontextmanager
async def default_mutation_repositories() -> AsyncIterator[
    tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
]:
    """Default repository factory yielding approval and mutation repositories."""
    factory = get_session_factory()
    async with factory() as session:
        yield (ApprovalRepository(session), MutationRepository(session))


class GitMutationServiceProtocol(Protocol):
    """Interface for Git mutation and Pull Request creation orchestration."""

    async def create_pull_request(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> MutationRecord:
        """Executes full mutation pipeline creating PR for approved fix."""
        ...


class GitMutationService:
    """Orchestrates controlled git mutation, pre-push validation, and GitHub PR creation."""

    def __init__(
        self,
        repository_factory: (
            Callable[
                [],
                AbstractAsyncContextManager[
                    tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
                ],
            ]
            | None
        ) = None,
        github_client: GitHubClientProtocol | None = None,
        repo_checkout: RepoCheckoutProtocol | None = None,
        sandbox_runner: SandboxRunnerProtocol | None = None,
    ) -> None:
        self.repository_factory = repository_factory or default_mutation_repositories
        self.github_client = github_client or GitHubClient()
        self.repo_checkout = repo_checkout or GitRepositoryCheckoutManager()
        self.sandbox_runner = sandbox_runner or DockerSandboxRunner()
        self.validation_service = ValidationService(
            sandbox_runner=self.sandbox_runner,
            checkout_manager=self.repo_checkout,
        )

    def generate_branch_name(self, incident_id: str, proposal_id: str) -> str:
        """Generates a deterministic, collision-resistant fix branch name."""
        clean_inc = re.sub(r"[^a-zA-Z0-9_\-]", "", incident_id)
        clean_prop = re.sub(r"[^a-zA-Z0-9_\-]", "", proposal_id)
        branch = f"akesis/fix/{clean_inc}/{clean_prop}"
        return branch

    def validate_branch_name(self, branch_name: str) -> None:
        """Enforces branch name safety constraints."""
        if not branch_name or not SAFE_BRANCH_NAME_RE.match(branch_name):
            raise UnsafeBranchError(f"Branch name '{branch_name}' contains invalid characters.")
        if ".." in branch_name or branch_name.startswith("/") or branch_name.endswith("/"):
            raise UnsafeBranchError(
                f"Branch name '{branch_name}' contains directory traversal or invalid slashes."
            )
        if branch_name.lower() in PROTECTED_BRANCHES:
            raise UnsafeBranchError(f"Branch name '{branch_name}' collides with protected branch.")

    def verify_preflight_invariants(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> None:
        """Strictly verifies that the proposal and approval meet all non-negotiable criteria."""
        if not proposal.is_valid or proposal.status != "proposed":
            raise PreflightCheckError("FixProposal is not valid or not in 'proposed' state.")

        if validation.status != ValidationStatus.PASSED or validation.exit_code != 0:
            raise PreflightCheckError(
                f"ValidationResult status must be 'passed' with exit code 0 "
                f"(got {validation.status}, {validation.exit_code})."
            )

        if approval.status != ApprovalStatus.APPROVED:
            raise PreflightCheckError(
                f"ApprovalRecord status must be 'approved' (got {approval.status})."
            )

        if approval.proposal_id != proposal.proposal_id:
            raise PreflightCheckError(
                f"Approval proposal_id '{approval.proposal_id}' does not match "
                f"proposal '{proposal.proposal_id}'."
            )

        if approval.commit_sha != proposal.commit_sha:
            raise PreflightCheckError(
                f"Approval commit_sha '{approval.commit_sha}' does not match "
                f"proposal '{proposal.commit_sha}'."
            )

        if context.commit_sha != proposal.commit_sha:
            raise PreflightCheckError(
                f"FailureContext commit_sha '{context.commit_sha}' does not match "
                f"proposal '{proposal.commit_sha}'."
            )

    async def create_pull_request(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> MutationRecord:
        """Executes full mutation pipeline creating PR for approved fix."""
        # 1. Preflight safety checks
        self.verify_preflight_invariants(context, proposal, validation, approval)

        branch_name = self.generate_branch_name(context.incident_id, proposal.proposal_id)
        self.validate_branch_name(branch_name)

        mutation_id = f"mut_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)

        async with self.repository_factory() as (approval_repo, mutation_repo):
            # 2. Idempotency Check: Existing mutation in database
            existing_mut = await mutation_repo.get_by_proposal_and_commit(
                proposal.proposal_id, proposal.commit_sha
            )
            if existing_mut and existing_mut.status == MutationStatus.PR_CREATED:
                logger.info(
                    "mutation_idempotent_existing_returned",
                    mutation_id=existing_mut.mutation_id,
                    pr_number=existing_mut.pr_number,
                )
                return existing_mut

            # Check GitHub for existing open PR
            existing_pr = await self.github_client.find_pull_request(
                owner=context.repository_owner,
                repo=context.repository_name,
                head_branch=branch_name,
                base_branch=context.branch,
            )
            if existing_pr:
                pr_num = existing_pr.get("number", 0)
                pr_html = existing_pr.get("html_url", "")
                record = MutationRecord(
                    mutation_id=mutation_id,
                    proposal_id=proposal.proposal_id,
                    approval_id=approval.approval_id,
                    incident_id=context.incident_id,
                    repository_owner=context.repository_owner,
                    repository_name=context.repository_name,
                    base_commit_sha=proposal.commit_sha,
                    branch_name=branch_name,
                    resulting_commit_sha=existing_pr.get("head", {}).get("sha"),
                    validation_status=ValidationStatus.PASSED,
                    pr_number=pr_num,
                    pr_url=pr_html,
                    status=MutationStatus.PR_CREATED,
                    created_at=now,
                    updated_at=now,
                )
                await mutation_repo.create_mutation(record)
                return record

            # Initialize mutation record
            record = MutationRecord(
                mutation_id=mutation_id,
                proposal_id=proposal.proposal_id,
                approval_id=approval.approval_id,
                incident_id=context.incident_id,
                repository_owner=context.repository_owner,
                repository_name=context.repository_name,
                base_commit_sha=proposal.commit_sha,
                branch_name=branch_name,
                status=MutationStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            await mutation_repo.create_mutation(record)

        # 3. Create isolated ephemeral mutation workspace
        temp_dir = Path(tempfile.mkdtemp(prefix="akesis_mut_"))
        try:
            # Checkout repository source
            source_dir = self.repo_checkout.checkout_commit(
                repo_owner=context.repository_owner,
                repo_name=context.repository_name,
                commit_sha=proposal.commit_sha,
            )

            # Copy tree to ephemeral mutation workspace
            shutil.copytree(source_dir, temp_dir, dirs_exist_ok=True)
            repo = git.Repo(temp_dir)

            # Verify exact HEAD SHA matches approved proposal SHA
            head_sha = repo.head.commit.hexsha
            if head_sha != proposal.commit_sha:
                err_msg = (
                    f"Stale commit detected: Checked out HEAD ({head_sha}) does "
                    f"not match approved proposal commit ({proposal.commit_sha})."
                )
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id, MutationStatus.FAILED, failure_reason=err_msg
                    )
                raise StaleCommitError(err_msg)

            # 4. Create dedicated fix branch
            current_branches = [h.name for h in repo.heads]
            if branch_name in current_branches:
                repo.git.checkout(branch_name)
            else:
                repo.git.checkout("-b", branch_name)

            # 5. Apply unified diff patch verbatim
            async with self.repository_factory() as (_, mutation_repo):
                await mutation_repo.update_status(mutation_id, MutationStatus.APPLYING)

            patch_file = temp_dir / "fix.patch"
            patch_file.write_text(proposal.unified_diff, encoding="utf-8")

            # Check patch applicability
            check_res = subprocess.run(
                ["git", "apply", "--check", str(patch_file)],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if check_res.returncode != 0:
                err_msg = f"Patch check failed: {check_res.stderr.strip()}"
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id, MutationStatus.FAILED, failure_reason=err_msg
                    )
                raise MutationError(err_msg)

            # Apply patch
            apply_res = subprocess.run(
                ["git", "apply", str(patch_file)],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if apply_res.returncode != 0:
                err_msg = f"Patch apply failed: {apply_res.stderr.strip()}"
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id, MutationStatus.FAILED, failure_reason=err_msg
                    )
                raise MutationError(err_msg)

            # Remove temp patch file so it is not in working tree
            if patch_file.exists():
                patch_file.unlink()

            # 6. Verify working tree matches proposal scope exactly
            diff_files = [item.a_path for item in repo.index.diff(None)]
            untracked = repo.untracked_files

            if untracked:
                err_msg = f"Unexpected untracked files in working tree: {untracked}"
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id, MutationStatus.FAILED, failure_reason=err_msg
                    )
                raise WorkingTreeMismatchError(err_msg)

            target_set = set(proposal.target_files)
            changed_set = set(diff_files)
            if not changed_set.issubset(target_set):
                err_msg = f"Working tree changes {changed_set} exceed target scope {target_set}"
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id, MutationStatus.FAILED, failure_reason=err_msg
                    )
                raise WorkingTreeMismatchError(err_msg)

            # 7. Post-patch pre-push validation
            post_val = await self.validation_service.validate_fix(
                proposal=proposal, context=context, repo_root=temp_dir
            )
            if post_val.status != ValidationStatus.PASSED or post_val.exit_code != 0:
                err_msg = (
                    f"Pre-push validation failed "
                    f"(status: {post_val.status}, exit code: {post_val.exit_code})"
                )
                async with self.repository_factory() as (_, mutation_repo):
                    await mutation_repo.update_status(
                        mutation_id,
                        MutationStatus.FAILED,
                        validation_status=post_val.status,
                        failure_reason=err_msg,
                    )
                raise PrePushValidationError(err_msg)

            async with self.repository_factory() as (_, mutation_repo):
                await mutation_repo.update_status(
                    mutation_id,
                    MutationStatus.VALIDATED,
                    validation_status=ValidationStatus.PASSED,
                )

            # 8. Deterministic Commit
            repo.git.add("-A")
            commit_msg = (
                f"fix(akesis): apply approved remediation {proposal.proposal_id}\n\n"
                f"Incident: {context.incident_id}\n"
                f"Approval: {approval.approval_id}\n"
                f"Reviewer: {approval.decided_by or 'human_reviewer'}\n"
                f"Confidence: {proposal.confidence_score:.2f}\n"
            )
            author = git.Actor("Akesis Self-Healing Agent", "akesis@crlabs.ai")
            new_commit = repo.index.commit(commit_msg, author=author, committer=author)
            resulting_sha = new_commit.hexsha

            async with self.repository_factory() as (_, mutation_repo):
                await mutation_repo.update_status(
                    mutation_id,
                    MutationStatus.COMMITTED,
                    resulting_commit_sha=resulting_sha,
                )

            # 9. Push to GitHub remote
            self._push_branch_safely(repo, branch_name)

            async with self.repository_factory() as (_, mutation_repo):
                await mutation_repo.update_status(mutation_id, MutationStatus.PUSHED)

            # 10. Create GitHub Pull Request
            target_desc = context.signal.target_file or "repo"
            pr_title = f"fix(ci): fix {context.signal.error_type} in {target_desc}"
            pr_body = self._build_pr_body(context, proposal, validation, approval, resulting_sha)

            pr_data = await self.github_client.create_pull_request(
                owner=context.repository_owner,
                repo=context.repository_name,
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=context.branch,
            )

            pr_number = pr_data.get("number", 1)
            pr_url = pr_data.get(
                "html_url",
                f"https://github.com/{context.repository_owner}/{context.repository_name}/pull/{pr_number}",
            )

            # 11. Finalize mutation record state
            async with self.repository_factory() as (_, mutation_repo):
                updated_record = await mutation_repo.update_status(
                    mutation_id,
                    MutationStatus.PR_CREATED,
                    pr_number=pr_number,
                    pr_url=pr_url,
                )
                if updated_record is None:
                    raise MutationError(f"Failed to finalize mutation record '{mutation_id}'")

            logger.info(
                "mutation_pipeline_completed_successfully",
                mutation_id=mutation_id,
                pr_number=pr_number,
                branch=branch_name,
            )
            return updated_record

        finally:
            # Clean up ephemeral working tree
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _push_branch_safely(self, repo: git.Repo, branch_name: str) -> None:
        """Executes safe git push for the dedicated fix branch."""
        try:
            if "origin" in [r.name for r in repo.remotes]:
                # If remote is configured in local repo
                repo.git.push("origin", branch_name)
            else:
                logger.info("simulated_git_push_completed", branch=branch_name)
        except Exception as err:
            logger.error("git_push_failed", branch=branch_name, error=str(err))
            raise GitPushError(f"Failed to push branch '{branch_name}': {err}") from err

    def _build_pr_body(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
        resulting_sha: str,
    ) -> str:
        """Builds authoritative structured Markdown description for the Pull Request."""
        return (
            f"## 🤖 Akesis CI Self-Healing Remediation\n\n"
            f"This pull request contains a verified code remediation generated by Akesis "
            f"and **explicitly authorized by a human engineer**.\n\n"
            f"### 📋 Incident & Approval Metadata\n"
            f"* **Incident ID:** `{context.incident_id}`\n"
            f"* **Workflow Run ID:** [#{context.run_id}]({context.run_url})\n"
            f"* **Failure Category:** `{context.signal.category}`\n"
            f"* **Base Commit SHA:** `{proposal.commit_sha}`\n"
            f"* **Resulting Commit SHA:** `{resulting_sha}`\n"
            f"* **Human Reviewer:** `{approval.decided_by or 'lead_engineer'}`\n"
            f"* **Approved At:** `{approval.decided_at or 'recently'}`\n\n"
            f"### 🛡️ Validation Outcome\n"
            f"* **Status:** `{validation.status.value.upper()}` "
            f"(Exit code `{validation.exit_code}`)\n"
            f"* **Validation Command:** `{validation.command_executed}`\n"
            f"* **Execution Time:** `{validation.duration_ms:.1f}ms`\n\n"
            f"### 💡 Remediation Rationale\n"
            f"{proposal.rationale}\n\n"
            f"### 🎯 Modified Files\n"
            + "\n".join(f"* `{f}`" for f in proposal.target_files)
            + "\n\n---\n"
            "*Generated by [Akesis Self-Healing CI/CD Agent](https://github.com/crlabs-ai/akesis)*"
        )
