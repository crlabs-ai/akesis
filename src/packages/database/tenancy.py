"""Workspace authorization for every tenant read and write (ADR-0012).

A `WorkspaceScope` is the only way to read tenant data through the repositories. It is produced
only by `authorize`, which checks the caller's membership in PostgreSQL on every request. So a
revoked member loses access on their next request, and a workspace ID chosen by the client grants
nothing by itself.

Tenancy follows the GitHub App installation: a pipeline (and its approval, mutation and logs)
belongs to the workspace that has linked the installation recorded in the pipeline's verified
provenance. Rows without a verified installation belong to no workspace.
"""

from dataclasses import dataclass, field

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import GitHubInstallationModel, WorkspaceMembershipModel
from src.packages.shared.workspace_roles import Role, at_least

_ISSUED_BY_AUTHORIZE = object()


class WorkspaceAccessDenied(Exception):
    """The caller is not a member, or lacks the role. Callers answer 404 for non-members so a
    workspace's existence is not revealed, and 403 for members without the role."""

    def __init__(self, *, is_member: bool) -> None:
        super().__init__("workspace access denied")
        self.is_member = is_member


@dataclass(frozen=True)
class WorkspaceScope:
    workspace_id: str
    user_id: str
    role: Role
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUED_BY_AUTHORIZE:
            raise TypeError("WorkspaceScope is issued only by tenancy.authorize()")

    def installation_ids(self) -> Select[tuple[int]]:
        """Subquery of the installations this workspace has linked."""
        return select(GitHubInstallationModel.installation_id).where(
            GitHubInstallationModel.workspace_id == self.workspace_id
        )


async def authorize(
    session: AsyncSession,
    *,
    user_id: str,
    workspace_id: str,
    minimum: Role = Role.VIEWER,
) -> WorkspaceScope:
    """Check membership now (never cached) and return a scope, or raise WorkspaceAccessDenied."""
    role_value = (
        await session.execute(
            select(WorkspaceMembershipModel.role).where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if role_value is None:
        raise WorkspaceAccessDenied(is_member=False)
    role = Role(role_value)
    if not at_least(role, minimum):
        raise WorkspaceAccessDenied(is_member=True)
    return WorkspaceScope(workspace_id, user_id, role, _ISSUED_BY_AUTHORIZE)
