"""Approval-gated remediation commit, branch and pull request (Phase 1.7).

The mutation never clones, never runs Git against a shared checkout, and never uses a personal
access token. Content comes from a verified snapshot of the approved commit; every remote object is
created through the GitHub Git Data API with a short-lived installation token that is scoped to the
verified event repository and held only in memory.

Order of checks (each fails closed):

1. Same-repository check on the verified provenance token, before any database, network, token or
   filesystem work. Fork-sourced runs (event repository != source repository) are refused.
2. Preflight invariants, the persisted approval, and idempotent reuse of the mutation record.
3. A fresh verified snapshot of the approved commit. The validated patch is applied verbatim to a
   workspace copy, changes are read back with links refused, scope is checked against the
   proposal, and the expected tree ID is computed locally.
4. Pre-push revalidation through the gated ValidationService.
5. Installation token (contents and pull_requests write, metadata read; event repository only).
   Repository metadata must match the verified ID and name, and must not be a fork or archived.

Pull request policy (same-repository runs only): the base is the exact verified source branch of the
failing workflow, and the head is the Akesis remediation branch in the same repository. When the
failing branch is the default branch, the PR therefore targets the default branch; the default
branch is otherwise never touched. The remediation commit has the approved commit as its single
parent, so the PR contains only the approved, validated change.

Freshness: the source branch must exist, be readable, and point at the approved full SHA. This is
checked before any remote object is created, immediately before the remediation branch is created,
and immediately before the pull request is created. A missing, inaccessible or moved source branch
aborts (StaleCommitError or GitPushError); nothing is rebased and no other branch is substituted.

The remediation branch is only ever created. An existing branch is reused only when its head commit
has exactly the validated tree and the approved commit as its single parent; otherwise the mutation
stops. Akesis never merges a pull request.
"""

import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from src.packages.database.repositories import (
    ApprovalRepository,
    ApprovalRepositoryProtocol,
    MutationRepository,
    MutationRepositoryProtocol,
)
from src.packages.database.session import get_session_factory
from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError
from src.packages.sdk.github_git_source import API_BASE_URL
from src.packages.sdk.github_git_writer import GitHubRepositoryWriter, GitHubWriteError
from src.packages.sdk.sandbox_runner import (
    PatchApplicationError,
    SandboxRunnerProtocol,
    apply_patch_exact,
)
from src.packages.shared.config import settings
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
from src.packages.shared.persisted_provenance import (
    bind_work_to_context,
    verify_context_provenance,
)
from src.packages.shared.provenance import require_verified_provenance
from src.packages.shared.source_snapshot import (
    SnapshotPreparer,
    SnapshotRejected,
    SnapshotService,
    VerifiedSnapshot,
    WorkspaceChange,
    create_workspace,
    discard_workspace,
    snapshot_matches_token,
    tree_sha_after,
    workspace_changes,
)
from src.packages.shared.validation_service import ValidationService

logger = get_logger("akesis.mutation_service")

# Regex for safe branch names
SAFE_BRANCH_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\./]+$")
PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "release", "production", "staging"})

# The only permissions the mutation token requests. GitHub cannot grant more than the App holds.
MUTATION_TOKEN_PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}
COMMIT_AUTHOR = {"name": "Akesis Self-Healing Agent", "email": "akesis@crlabs.ai"}


class MutationError(Exception):
    """Base exception for Git mutation and PR creation failures."""

    pass


class PreflightCheckError(MutationError):
    """Raised when preconditions, approval bindings, or validation checks fail."""

    pass


class ForkSourceRefusedError(PreflightCheckError):
    """Raised for fork-sourced or cross-repository work. V1 mutates only same-repository runs."""

    pass


class StaleCommitError(MutationError):
    """Raised when the source branch no longer points to the approved commit."""

    pass


class UnsafeBranchError(MutationError):
    """Raised when branch name violates safety constraints or targets protected branch."""

    pass


class BranchConflictError(UnsafeBranchError):
    """Raised when the remediation branch already exists with other content. Never overwritten."""

    pass


class WorkingTreeMismatchError(MutationError):
    """Raised when patch creates unexpected file changes outside approved target scope."""

    pass


class PrePushValidationError(MutationError):
    """Raised when post-patch sandbox validation fails prior to push."""

    pass


class GitPushError(MutationError):
    """Raised when creating remote Git objects or the remediation branch fails."""

    pass


class PullRequestError(MutationError):
    """Raised when the pull request cannot be created, or an existing one does not match."""

    pass


@asynccontextmanager
async def default_mutation_repositories() -> AsyncIterator[
    tuple[ApprovalRepositoryProtocol, MutationRepositoryProtocol]
]:
    """Default repository factory yielding approval and mutation repositories."""
    factory = get_session_factory()
    async with factory() as session:
        yield (ApprovalRepository(session), MutationRepository(session))


def require_same_repository(context: FailureContext, token: Any) -> None:
    """Refuse fork-sourced or mismatched work. Pure: performs no I/O of any kind.

    The token is the verified provenance for this context. Its event and source repositories must
    be the same repository, and it must describe this exact incident, repository, commit and branch.
    """
    provenance = context.provenance
    if token is None or provenance is None:
        raise PreflightCheckError("Verified provenance is required for mutation")
    if (
        token.event_repository_id != token.source_repository_id
        or provenance.event_repository.id != provenance.source_repository.id
        or provenance.event_repository.full_name != provenance.source_repository.full_name
    ):
        raise ForkSourceRefusedError("Fork-sourced runs are not mutated in V1")
    full_name = f"{context.repository_owner}/{context.repository_name}"
    if (
        token.event_repository_id != provenance.event_repository.id
        or token.incident_id != context.incident_id
        or f"{token.owner}/{token.name}" != full_name
        or provenance.event_repository.full_name != full_name
        or token.head_sha != context.commit_sha
        or token.head_branch != context.branch
    ):
        raise PreflightCheckError("Verified provenance does not describe this incident")


@dataclass(frozen=True)
class _PreparedChange:
    changes: list[WorkspaceChange]
    base_tree: str
    expected_tree: str


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
    """Creates the approved remediation as a branch and PR in the verified event repository."""

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
        *,
        auth: Any = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        snapshot_service: SnapshotPreparer | None = None,
        sandbox_runner: SandboxRunnerProtocol | None = None,
    ) -> None:
        self.repository_factory = repository_factory or default_mutation_repositories
        self._auth = auth
        self._client_factory = client_factory or self._default_client
        self.snapshot_service = snapshot_service or SnapshotService()
        self.validation_service = ValidationService(
            sandbox_runner=sandbox_runner, snapshot_service=self.snapshot_service
        )

    @staticmethod
    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0)

    def _authenticator(self, client: httpx.AsyncClient) -> Any:
        if self._auth is not None:
            return self._auth
        if not settings.github_app_id or not settings.github_app_private_key:
            raise MutationError("GitHub App authentication is not configured")
        return GitHubAppAuth(settings.github_app_id, settings.github_app_private_key, client=client)

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
        if not re.fullmatch(r"[0-9a-f]{40}", context.commit_sha):
            raise PreflightCheckError("Commit SHA must be a full lowercase SHA-1")
        if not (
            context.incident_id
            == proposal.incident_id
            == validation.incident_id
            == approval.incident_id
        ):
            raise PreflightCheckError("Incident identifiers do not match")
        if (
            validation.proposal_id != proposal.proposal_id
            or validation.commit_sha != proposal.commit_sha
        ):
            raise PreflightCheckError("Validation does not match proposal")

    async def create_pull_request(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
    ) -> MutationRecord:
        """Executes full mutation pipeline creating PR for approved fix.

        Fails closed before any preflight, snapshot, token or GitHub call while the provenance
        hold is active.
        """
        require_verified_provenance()  # first: no database read or preflight before the hold
        provenance = await verify_context_provenance(context)
        bind_work_to_context(context, proposal, validation, approval)
        return await self._create_pull_request_unchecked(
            context, proposal, validation, approval, provenance
        )

    async def _create_pull_request_unchecked(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
        provenance: object | None = None,
    ) -> MutationRecord:
        """Mutation body. Private: callers must use the gated public method."""
        require_same_repository(context, provenance)  # no I/O happens before this check
        bind_work_to_context(context, proposal, validation, approval)
        self.verify_preflight_invariants(context, proposal, validation, approval)

        branch_name = self.generate_branch_name(context.incident_id, proposal.proposal_id)
        self.validate_branch_name(branch_name)

        record = await self._claim_record(context, proposal, approval, branch_name)
        if record.status == MutationStatus.PR_CREATED:
            return record
        try:
            return await self._mutate(record, context, proposal, validation, approval, provenance)
        except Exception as error:
            reason = str(error) or type(error).__name__
            async with self.repository_factory() as (_, mutation_repo):
                await mutation_repo.update_status(
                    record.mutation_id, MutationStatus.FAILED, failure_reason=reason[:1000]
                )
            raise

    async def _claim_record(
        self,
        context: FailureContext,
        proposal: FixProposal,
        approval: ApprovalRecord,
        branch_name: str,
    ) -> MutationRecord:
        """Return the existing record for this proposal and commit, or create one. Never two."""
        async with self.repository_factory() as (approval_repo, mutation_repo):
            persisted_approval = await approval_repo.get_approval(approval.approval_id)
            if persisted_approval is None or persisted_approval != approval:
                raise PreflightCheckError("Approval does not match persisted approval")
            existing = await mutation_repo.get_by_proposal_and_commit(
                proposal.proposal_id, proposal.commit_sha
            )
            if existing is not None:
                if (
                    existing.incident_id != context.incident_id
                    or existing.approval_id != approval.approval_id
                    or existing.repository_owner != context.repository_owner
                    or existing.repository_name != context.repository_name
                ):
                    raise PreflightCheckError("Existing mutation record belongs to other work")
                if existing.status == MutationStatus.PR_CREATED:
                    logger.info(
                        "mutation_idempotent_existing_returned",
                        mutation_id=existing.mutation_id,
                        pr_number=existing.pr_number,
                    )
                    return existing
                resumed = await mutation_repo.update_status(
                    existing.mutation_id, MutationStatus.PENDING
                )
                return resumed or existing
            now = datetime.now(UTC)
            record = MutationRecord(
                mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
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
            return await mutation_repo.create_mutation(record)

    async def _status(self, mutation_id: str, status: MutationStatus, **fields: Any) -> None:
        async with self.repository_factory() as (_, mutation_repo):
            await mutation_repo.update_status(mutation_id, status, **fields)

    async def _prepare_change(self, token: Any, proposal: FixProposal) -> _PreparedChange:
        """Apply the validated patch to a verified snapshot copy and describe the exact result."""
        try:
            async with self.snapshot_service.prepare(token) as snapshot:
                if not isinstance(snapshot, VerifiedSnapshot) or not snapshot_matches_token(
                    token, snapshot
                ):
                    raise PreflightCheckError("Snapshot does not match the verified provenance")
                workspace = create_workspace(snapshot)
                try:
                    try:
                        apply_patch_exact(workspace, proposal.unified_diff)
                    except PatchApplicationError as error:
                        raise MutationError(f"Patch apply failed: {error}") from None
                    changes = workspace_changes(snapshot, workspace)
                    expected_tree = tree_sha_after(snapshot, changes)
                    base_tree = snapshot.root_tree_sha
                finally:
                    discard_workspace(workspace)
        except SnapshotRejected as error:
            if error.code == "symlink":
                raise WorkingTreeMismatchError(
                    "Symbolic links are not permitted in mutations"
                ) from None
            raise MutationError(f"Source snapshot rejected ({error.code})") from None

        changed = {change.path for change in changes}
        if not changed:
            raise MutationError("Patch produced no changes")
        allowed = set(proposal.target_files)
        if not changed.issubset(allowed):
            raise WorkingTreeMismatchError(
                f"Working tree changes {sorted(changed)} exceed target scope {sorted(allowed)}"
            )
        return _PreparedChange(changes=changes, base_tree=base_tree, expected_tree=expected_tree)

    async def _mutate(
        self,
        record: MutationRecord,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        approval: ApprovalRecord,
        token: Any,
    ) -> MutationRecord:
        mutation_id = record.mutation_id
        branch_name = self.generate_branch_name(context.incident_id, proposal.proposal_id)
        approved_sha = proposal.commit_sha

        await self._status(mutation_id, MutationStatus.APPLYING)
        prepared = await self._prepare_change(token, proposal)

        post_val = await self.validation_service.validate_fix(proposal=proposal, context=context)
        if post_val.status != ValidationStatus.PASSED or post_val.exit_code != 0:
            await self._status(
                mutation_id, MutationStatus.FAILED, validation_status=post_val.status
            )
            raise PrePushValidationError(
                f"Pre-push validation failed "
                f"(status: {post_val.status}, exit code: {post_val.exit_code})"
            )
        await self._status(
            mutation_id, MutationStatus.VALIDATED, validation_status=ValidationStatus.PASSED
        )

        phase = "authentication"
        try:
            async with self._client_factory() as client:
                installation = await self._authenticator(client).installation_token(
                    token.installation_id,
                    repository_ids=[token.event_repository_id],
                    permissions=dict(MUTATION_TOKEN_PERMISSIONS),
                )
                writer = GitHubRepositoryWriter(
                    client,
                    installation.token,
                    repository_id=token.event_repository_id,
                    full_name=f"{token.owner}/{token.name}",
                )

                phase = "repository metadata"
                repository = await writer.get_repository()
                if repository.get("fork") is not False:
                    raise ForkSourceRefusedError(
                        "Target repository is a fork; V1 does not mutate it"
                    )
                if repository.get("archived") is not False:
                    raise MutationError("Target repository is archived")
                source_branch = token.head_branch  # the verified failing branch is the PR base
                if source_branch == branch_name:
                    raise UnsafeBranchError("Source branch collides with the remediation branch")

                phase = "freshness check"
                await self._require_fresh(writer, source_branch, approved_sha)

                phase = "remediation branch"
                commit_sha = await self._existing_branch_head(
                    writer, branch_name, prepared, approved_sha
                )
                if commit_sha is None:
                    phase = "git objects"
                    commit_sha = await self._write_commit(
                        writer, prepared, approved_sha, context, proposal, approval
                    )
                    await self._status(
                        mutation_id, MutationStatus.COMMITTED, resulting_commit_sha=commit_sha
                    )
                    phase = "freshness check"
                    await self._require_fresh(writer, source_branch, approved_sha)
                    phase = "remediation branch"
                    if not await writer.create_branch(branch_name, commit_sha):
                        # Created concurrently: accept it only if it is exactly this remediation.
                        commit_sha = await self._existing_branch_head(
                            writer, branch_name, prepared, approved_sha
                        )
                        if commit_sha is None:
                            raise BranchConflictError("Remediation branch could not be created")
                await self._status(
                    mutation_id, MutationStatus.PUSHED, resulting_commit_sha=commit_sha
                )
                logger.info(
                    "remediation_branch_ready",
                    mutation_id=mutation_id,
                    branch=branch_name,
                    commit_sha=commit_sha,
                )

                phase = "freshness check"
                await self._require_fresh(writer, source_branch, approved_sha)
                phase = "pull request"
                pull = await self._open_pull_request(
                    writer,
                    branch_name=branch_name,
                    base_branch=source_branch,
                    commit_sha=commit_sha,
                    title=self._pr_title(context),
                    body=self._build_pr_body(
                        context, proposal, validation, post_val, approval, commit_sha
                    ),
                )
        except GitHubAppAuthenticationError as error:
            raise MutationError(f"GitHub App authentication failed ({error.code})") from None
        except GitHubWriteError as error:
            failure = PullRequestError if phase == "pull request" else GitPushError
            raise failure(f"GitHub request failed during {phase} ({error.code})") from None

        async with self.repository_factory() as (_, mutation_repo):
            updated = await mutation_repo.update_status(
                mutation_id,
                MutationStatus.PR_CREATED,
                resulting_commit_sha=commit_sha,
                pr_number=pull["number"],
                pr_url=pull["html_url"],
            )
            if updated is None:
                raise MutationError(f"Failed to finalize mutation record '{mutation_id}'")
        logger.info(
            "mutation_pipeline_completed_successfully",
            mutation_id=mutation_id,
            pr_number=pull["number"],
            branch=branch_name,
        )
        return updated

    async def _require_fresh(
        self, writer: GitHubRepositoryWriter, source_branch: str, approved_sha: str
    ) -> None:
        """Fail unless the verified source branch exists and still points at the approved SHA."""
        head = await writer.branch_head(source_branch)
        if head is None:
            raise StaleCommitError(f"Source branch '{source_branch}' no longer exists")
        if head != approved_sha:
            raise StaleCommitError(
                f"Source branch '{source_branch}' no longer points to the approved commit"
            )

    async def _existing_branch_head(
        self,
        writer: GitHubRepositoryWriter,
        branch_name: str,
        prepared: _PreparedChange,
        approved_sha: str,
    ) -> str | None:
        """Head of an existing remediation branch, only if it is exactly this remediation."""
        head = await writer.branch_head(branch_name)
        if head is None:
            return None
        tree, parents = await writer.get_commit(head)
        if tree != prepared.expected_tree or parents != [approved_sha]:
            raise BranchConflictError(
                f"Branch '{branch_name}' exists with other content and is never overwritten"
            )
        return head

    async def _write_commit(
        self,
        writer: GitHubRepositoryWriter,
        prepared: _PreparedChange,
        approved_sha: str,
        context: FailureContext,
        proposal: FixProposal,
        approval: ApprovalRecord,
    ) -> str:
        entries: list[dict[str, Any]] = []
        for change in prepared.changes:
            if change.content is None:
                entries.append({"path": change.path, "mode": "100644", "type": "blob", "sha": None})
                continue
            sha = await writer.create_blob(change.content)
            entries.append({"path": change.path, "mode": change.mode, "type": "blob", "sha": sha})
        tree = await writer.create_tree(prepared.base_tree, entries, prepared.expected_tree)
        message = (
            f"fix(akesis): apply approved remediation {proposal.proposal_id}\n\n"
            f"Incident: {context.incident_id}\n"
            f"Approval: {approval.approval_id}\n"
            f"Reviewer: {approval.decided_by or 'not recorded'}\n"
            f"Confidence: {proposal.confidence_score:.2f}\n"
        )
        return await writer.create_commit(message, tree, approved_sha, dict(COMMIT_AUTHOR))

    async def _open_pull_request(
        self,
        writer: GitHubRepositoryWriter,
        *,
        branch_name: str,
        base_branch: str,
        commit_sha: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        """Reuse the one matching open PR, or open a new one. Any other existing PR stops here."""
        existing = await writer.find_pull_requests(branch_name, base_branch)
        if len(existing) > 1:
            raise PullRequestError("More than one pull request exists for the remediation branch")
        if existing:
            if existing[0].get("state") != "open":
                raise PullRequestError("A closed remediation pull request exists; not reopened")
            pull = self._checked_pull(writer, existing[0], branch_name, base_branch, commit_sha)
            logger.info("mutation_existing_pull_request_reused", pr_number=pull["number"])
            return pull
        created = await writer.create_pull_request(title, body, branch_name, base_branch)
        return self._checked_pull(writer, created, branch_name, base_branch, commit_sha)

    @staticmethod
    def _checked_pull(
        writer: GitHubRepositoryWriter,
        pull: dict[str, Any],
        branch_name: str,
        base_branch: str,
        commit_sha: str,
    ) -> dict[str, Any]:
        head, base = pull.get("head") or {}, pull.get("base") or {}
        number = pull.get("number")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or head.get("ref") != branch_name
            or head.get("sha") != commit_sha
            or (head.get("repo") or {}).get("id") != writer.repository_id
            or base.get("ref") != base_branch
            or (base.get("repo") or {}).get("id") != writer.repository_id
            or pull.get("html_url") != f"https://github.com/{writer.full_name}/pull/{number}"
        ):
            raise PullRequestError("Pull request does not match the remediation")
        return pull

    @staticmethod
    def _pr_title(context: FailureContext) -> str:
        subject = context.signal.error_type or context.signal.category
        return f"fix(ci): fix {subject} in {context.signal.target_file or 'repo'}"

    def _build_pr_body(
        self,
        context: FailureContext,
        proposal: FixProposal,
        validation: ValidationResult,
        pre_push: ValidationResult,
        approval: ApprovalRecord,
        resulting_sha: str,
    ) -> str:
        """Describe only what the persisted records and this run actually established."""
        return (
            "## Akesis CI remediation\n\n"
            "This pull request applies a fix proposed by Akesis and approved by a human reviewer. "
            "Akesis never merges it: a maintainer must review and merge it.\n\n"
            "### Incident\n"
            f"* **Incident ID:** `{context.incident_id}`\n"
            f"* **Workflow run:** [#{context.run_id}]({context.run_url})\n"
            f"* **Failure category:** `{context.signal.category}`\n"
            f"* **Base (failing source) branch:** `{context.branch}`\n"
            f"* **Approved base commit:** `{proposal.commit_sha}`\n"
            f"* **Remediation commit:** `{resulting_sha}`\n"
            f"* **Approved by:** `{approval.decided_by or 'not recorded'}`\n"
            f"* **Approved at:** `{approval.decided_at or 'not recorded'}`\n\n"
            "### Validation\n"
            f"* **Validation before approval:** `{validation.status.value.upper()}` "
            f"(exit code `{validation.exit_code}`, command `{validation.command_executed}`)\n"
            f"* **Revalidation before push:** `{pre_push.status.value.upper()}` "
            f"(exit code `{pre_push.exit_code}`, command `{pre_push.command_executed}`)\n\n"
            "### Rationale\n"
            f"{proposal.rationale}\n\n"
            "### Modified files\n" + "\n".join(f"* `{f}`" for f in proposal.target_files) + "\n"
        )
