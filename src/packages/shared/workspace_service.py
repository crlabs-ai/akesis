"""Workspaces, members, invitations, Slack mappings and GitHub installations (ADR-0012).

Every operation takes a `WorkspaceScope` (issued by tenancy.authorize after a fresh membership
check) or the signed-in user, and enforces the role rules in workspace_roles itself. Routes add
nothing to authorization; they only translate errors to HTTP.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.packages.database.models import (
    GitHubInstallationModel,
    PipelineModel,
    UserModel,
    WorkspaceInvitationModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
    WorkspaceRepositoryModel,
)
from src.packages.database.tenancy import WorkspaceScope
from src.packages.shared.auth_service import CurrentUser, new_id
from src.packages.shared.logging import get_logger
from src.packages.shared.workspace_roles import (
    Role,
    can_approve,
    can_assign,
    can_manage,
    can_manage_members,
)

logger = get_logger("akesis.workspaces")

INVITATION_TTL = timedelta(days=7)
MAX_INSTALLATION_PAGES = 10
MAX_REPOSITORY_PAGES = 30
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_SLACK_USER = re.compile(r"^[UW][A-Z0-9]{2,31}$")
_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


class WorkspaceError(Exception):
    """A rule violation. `code` is safe to show the user."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _forbidden(code: str = "insufficient_role") -> WorkspaceError:
    return WorkspaceError(code, 403)


@dataclass(frozen=True)
class WorkspaceSummary:
    workspace_id: str
    kind: str
    name: str
    slug: str
    role: str


async def _workspace(db: AsyncSession, scope: WorkspaceScope) -> WorkspaceModel:
    return (
        await db.execute(
            select(WorkspaceModel).where(WorkspaceModel.workspace_id == scope.workspace_id)
        )
    ).scalar_one()


async def list_my_workspaces(db: AsyncSession, user_id: str) -> list[WorkspaceSummary]:
    rows = (
        await db.execute(
            select(WorkspaceModel, WorkspaceMembershipModel.role)
            .join(
                WorkspaceMembershipModel,
                WorkspaceMembershipModel.workspace_id == WorkspaceModel.workspace_id,
            )
            .where(WorkspaceMembershipModel.user_id == user_id)
            .order_by(WorkspaceModel.kind.desc(), WorkspaceModel.name)  # personal first
        )
    ).all()
    return [WorkspaceSummary(ws.workspace_id, ws.kind, ws.name, ws.slug, role) for ws, role in rows]


async def create_team_workspace(
    db: AsyncSession, actor: CurrentUser, name: str
) -> WorkspaceSummary:
    clean = " ".join(name.split())
    if not 2 <= len(clean) <= 100:
        raise WorkspaceError("invalid_workspace_name")
    base = _SLUG_CHARS.sub("-", clean.lower()).strip("-")[:48] or "team"
    now = datetime.now(UTC)
    workspace = WorkspaceModel(
        workspace_id=new_id("ws"),
        kind="team",
        name=clean,
        slug=f"{base}-{secrets.token_hex(3)}",
        created_by=actor.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(workspace)
    db.add(
        WorkspaceMembershipModel(
            workspace_id=workspace.workspace_id,
            user_id=actor.user_id,
            role=Role.OWNER.value,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    logger.info("team_workspace_created", workspace_id=workspace.workspace_id, by=actor.user_id)
    return WorkspaceSummary(workspace.workspace_id, "team", clean, workspace.slug, Role.OWNER.value)


async def rename_workspace(db: AsyncSession, scope: WorkspaceScope, name: str) -> None:
    if scope.role != Role.OWNER:
        raise _forbidden()
    clean = " ".join(name.split())
    if not 2 <= len(clean) <= 100:
        raise WorkspaceError("invalid_workspace_name")
    workspace = await _workspace(db, scope)
    if workspace.kind == "personal":
        raise WorkspaceError("personal_workspace")
    workspace.name = clean
    await db.commit()


# --- Members ----------------------------------------------------------------------------------


async def list_members(db: AsyncSession, scope: WorkspaceScope) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(WorkspaceMembershipModel, UserModel)
            .join(UserModel, UserModel.user_id == WorkspaceMembershipModel.user_id)
            .where(WorkspaceMembershipModel.workspace_id == scope.workspace_id)
            .order_by(UserModel.github_login)
        )
    ).all()
    # Slack mappings are an approval credential: only member managers see them.
    show_slack = can_manage_members(scope.role)
    logins = {user.user_id: user.github_login for _, user in rows}
    return [
        {
            "user_id": user.user_id,
            "github_login": user.github_login,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "role": membership.role,
            "slack_user_id": membership.slack_user_id if show_slack else None,
            "slack_mapped_by_login": (
                logins.get(membership.slack_mapped_by)
                if show_slack and membership.slack_mapped_by
                else None
            ),
            "slack_mapped_at": membership.slack_mapped_at if show_slack else None,
            "joined_at": membership.created_at,
        }
        for membership, user in rows
    ]


async def _member(db: AsyncSession, workspace_id: str, user_id: str) -> WorkspaceMembershipModel:
    membership = (
        await db.execute(
            select(WorkspaceMembershipModel)
            .where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.user_id == user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if membership is None:
        raise WorkspaceError("member_not_found", 404)
    return membership


async def _owner_count(db: AsyncSession, workspace_id: str) -> int:
    # Lock the owner rows so two concurrent demotions cannot both pass the last-owner check.
    owners = (
        await db.execute(
            select(WorkspaceMembershipModel.user_id)
            .where(
                WorkspaceMembershipModel.workspace_id == workspace_id,
                WorkspaceMembershipModel.role == Role.OWNER.value,
            )
            .with_for_update()
        )
    ).all()
    return len(owners)


async def change_role(
    db: AsyncSession, scope: WorkspaceScope, target_user_id: str, new_role: Role
) -> None:
    """Change another member's role. Nobody changes their own (no self-escalation)."""
    if target_user_id == scope.user_id:
        raise _forbidden("cannot_change_own_role")
    target = await _member(db, scope.workspace_id, target_user_id)
    if not can_manage(scope.role, Role(target.role)) or not can_assign(scope.role, new_role):
        await db.rollback()
        raise _forbidden()
    if (
        target.role == Role.OWNER.value
        and new_role != Role.OWNER
        and await _owner_count(db, scope.workspace_id) <= 1
    ):
        await db.rollback()
        raise WorkspaceError("last_owner", 409)
    target.role = new_role.value
    target.updated_at = datetime.now(UTC)
    await db.commit()
    logger.info(
        "member_role_changed",
        workspace_id=scope.workspace_id,
        target=target_user_id,
        role=new_role.value,
        by=scope.user_id,
    )


async def remove_member(db: AsyncSession, scope: WorkspaceScope, target_user_id: str) -> None:
    """Remove another member; access ends on their next request. Use `leave` for yourself."""
    if target_user_id == scope.user_id:
        raise WorkspaceError("use_leave")
    target = await _member(db, scope.workspace_id, target_user_id)
    if not can_manage(scope.role, Role(target.role)):
        await db.rollback()
        raise _forbidden()
    if target.role == Role.OWNER.value and await _owner_count(db, scope.workspace_id) <= 1:
        await db.rollback()
        raise WorkspaceError("last_owner", 409)
    await db.delete(target)
    await db.commit()
    logger.info(
        "member_removed", workspace_id=scope.workspace_id, target=target_user_id, by=scope.user_id
    )


async def leave_workspace(db: AsyncSession, scope: WorkspaceScope) -> None:
    workspace = await _workspace(db, scope)
    if workspace.kind == "personal":
        raise WorkspaceError("personal_workspace")
    me = await _member(db, scope.workspace_id, scope.user_id)
    if me.role == Role.OWNER.value and await _owner_count(db, scope.workspace_id) <= 1:
        await db.rollback()
        raise WorkspaceError("last_owner", 409)
    await db.delete(me)
    await db.commit()


async def set_slack_user(
    db: AsyncSession, scope: WorkspaceScope, target_user_id: str, slack_user_id: str | None
) -> None:
    """Create, change or revoke (None) a member's Slack approver mapping. Owner attestation:

    * only a workspace OWNER may set or revoke any mapping, including their own;
    * the owner's own Slack identity (mapped to them in any workspace) is never put on another
      member, so an owner cannot approve through someone else's account;
    * only members who may approve (APPROVER, MAINTAINER, ADMIN, OWNER) can be mapped;
    * the mapping records the attesting owner and time. Approval checks re-validate both.

    This is attestation by an accountable person, not proof that the member controls the Slack
    account; verified Slack identity is separate future work (ADR-0012)."""
    if scope.role != Role.OWNER:
        raise _forbidden("owner_required")
    if slack_user_id is not None and not _SLACK_USER.fullmatch(slack_user_id):
        raise WorkspaceError("invalid_slack_user_id")
    target = await _member(db, scope.workspace_id, target_user_id)
    if slack_user_id is None:
        target.slack_user_id = target.slack_mapped_by = target.slack_mapped_at = None
        target.updated_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "slack_mapping_revoked",
            workspace_id=scope.workspace_id,
            target=target_user_id,
            by=scope.user_id,
        )
        return
    if not can_approve(Role(target.role)):
        await db.rollback()
        raise WorkspaceError("member_cannot_approve", 400)
    if target_user_id != scope.user_id:
        own_identities = set(
            (
                await db.execute(
                    select(WorkspaceMembershipModel.slack_user_id).where(
                        WorkspaceMembershipModel.user_id == scope.user_id,
                        WorkspaceMembershipModel.slack_user_id.is_not(None),
                    )
                )
            ).scalars()
        )
        if slack_user_id in own_identities:
            await db.rollback()
            raise _forbidden("cannot_map_own_slack_identity")
    now = datetime.now(UTC)
    target.slack_user_id = slack_user_id
    target.slack_mapped_by = scope.user_id
    target.slack_mapped_at = now
    target.updated_at = now
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceError("slack_user_already_mapped", 409) from None
    logger.info(
        "slack_mapping_attested",
        workspace_id=scope.workspace_id,
        target=target_user_id,
        by=scope.user_id,
    )


# --- Invitations ------------------------------------------------------------------------------


async def resolve_github_user(
    github_api: httpx.AsyncClient, user_token: str, login: str
) -> dict[str, Any]:
    """Resolve a username to GitHub's immutable account ID, once, when the invitation is made."""
    clean = login.strip().lstrip("@")
    if not _LOGIN.fullmatch(clean):
        raise WorkspaceError("invalid_github_login")
    try:
        response = await github_api.get(
            f"/users/{clean}", headers=_github_headers(user_token), follow_redirects=False
        )
        if response.status_code == 404:
            raise WorkspaceError("github_user_not_found", 404)
        if response.status_code == 401:
            raise WorkspaceError("github_reauthorization_required", 401)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise WorkspaceError("github_unavailable", 502) from None
    user_id, resolved_login = data.get("id"), data.get("login")
    if type(user_id) is not int or user_id <= 0 or not isinstance(resolved_login, str):
        raise WorkspaceError("github_unavailable", 502)
    if data.get("type") != "User":
        raise WorkspaceError("github_account_not_a_user")  # organizations and bots cannot join
    avatar = data.get("avatar_url")
    return {
        "id": user_id,
        "login": resolved_login[:39],
        "avatar_url": avatar if isinstance(avatar, str) and avatar.startswith("https://") else None,
    }


async def invite(
    db: AsyncSession, scope: WorkspaceScope, target: dict[str, Any], role: Role
) -> dict[str, Any]:
    """Invite the GitHub account `target` (from resolve_github_user). The invitation is bound to
    its numeric ID, so a later username change can neither move nor steal it."""
    if not can_assign(scope.role, role):
        raise _forbidden()
    workspace = await _workspace(db, scope)
    if workspace.kind == "personal":
        raise WorkspaceError("personal_workspace")
    github_user_id: int = target["id"]
    already_member = (
        await db.execute(
            select(func.count())
            .select_from(WorkspaceMembershipModel)
            .join(UserModel, UserModel.user_id == WorkspaceMembershipModel.user_id)
            .where(
                WorkspaceMembershipModel.workspace_id == scope.workspace_id,
                UserModel.github_user_id == github_user_id,
            )
        )
    ).scalar_one()
    if already_member:
        raise WorkspaceError("already_member", 409)
    now = datetime.now(UTC)
    # An expired, unaccepted invitation would block a new one under the unique index; retire it.
    await db.execute(
        update(WorkspaceInvitationModel)
        .where(
            WorkspaceInvitationModel.workspace_id == scope.workspace_id,
            WorkspaceInvitationModel.github_user_id == github_user_id,
            WorkspaceInvitationModel.accepted_at.is_(None),
            WorkspaceInvitationModel.revoked_at.is_(None),
            WorkspaceInvitationModel.expires_at <= now,
        )
        .values(revoked_at=now)
    )
    invitation = WorkspaceInvitationModel(
        invitation_id=new_id("inv"),
        workspace_id=scope.workspace_id,
        github_user_id=github_user_id,
        github_login=target["login"],
        role=role.value,
        invited_by=scope.user_id,
        created_at=now,
        expires_at=now + INVITATION_TTL,
    )
    db.add(invitation)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceError("invitation_pending", 409) from None
    logger.info(
        "invitation_created",
        workspace_id=scope.workspace_id,
        github_user_id=github_user_id,
        by=scope.user_id,
    )
    return {**_invitation_view(invitation), "avatar_url": target.get("avatar_url")}


def _invitation_view(invitation: WorkspaceInvitationModel) -> dict[str, Any]:
    return {
        "invitation_id": invitation.invitation_id,
        "github_user_id": invitation.github_user_id,
        "github_login": invitation.github_login,
        "role": invitation.role,
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
    }


async def list_invitations(db: AsyncSession, scope: WorkspaceScope) -> list[dict[str, Any]]:
    if not can_manage_members(scope.role):
        raise _forbidden()
    rows = (
        await db.execute(
            select(WorkspaceInvitationModel)
            .where(
                WorkspaceInvitationModel.workspace_id == scope.workspace_id,
                WorkspaceInvitationModel.accepted_at.is_(None),
                WorkspaceInvitationModel.revoked_at.is_(None),
                WorkspaceInvitationModel.expires_at > datetime.now(UTC),
            )
            .order_by(WorkspaceInvitationModel.created_at.desc())
        )
    ).scalars()
    return [_invitation_view(row) for row in rows]


async def revoke_invitation(db: AsyncSession, scope: WorkspaceScope, invitation_id: str) -> None:
    if not can_manage_members(scope.role):
        raise _forbidden()
    result = await db.execute(
        update(WorkspaceInvitationModel)
        .where(
            WorkspaceInvitationModel.invitation_id == invitation_id,
            WorkspaceInvitationModel.workspace_id == scope.workspace_id,  # no cross-workspace ID
            WorkspaceInvitationModel.accepted_at.is_(None),
            WorkspaceInvitationModel.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
        .returning(WorkspaceInvitationModel.invitation_id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise WorkspaceError("invitation_not_found", 404)
    await db.commit()


async def my_invitations(db: AsyncSession, actor: CurrentUser) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(WorkspaceInvitationModel, WorkspaceModel)
            .join(
                WorkspaceModel,
                WorkspaceModel.workspace_id == WorkspaceInvitationModel.workspace_id,
            )
            .where(
                WorkspaceInvitationModel.github_user_id == actor.github_user_id,
                WorkspaceInvitationModel.accepted_at.is_(None),
                WorkspaceInvitationModel.revoked_at.is_(None),
                WorkspaceInvitationModel.expires_at > datetime.now(UTC),
            )
        )
    ).all()
    return [
        {**_invitation_view(inv), "workspace_id": ws.workspace_id, "workspace_name": ws.name}
        for inv, ws in rows
    ]


async def accept_invitation(db: AsyncSession, actor: CurrentUser, invitation_id: str) -> str:
    """Join with the invited role. Only the GitHub account the invitation was issued to, matched
    on its immutable numeric ID, may accept; usernames play no part."""
    now = datetime.now(UTC)
    invitation = (
        await db.execute(
            select(WorkspaceInvitationModel)
            .where(WorkspaceInvitationModel.invitation_id == invitation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        invitation is None
        or invitation.github_user_id != actor.github_user_id
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        await db.rollback()
        raise WorkspaceError("invitation_not_found", 404)
    invitation.accepted_at = now
    # An existing membership keeps its role: accepting never changes someone's privileges.
    await db.execute(
        insert(WorkspaceMembershipModel)
        .values(
            workspace_id=invitation.workspace_id,
            user_id=actor.user_id,
            role=invitation.role,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
    )
    await db.commit()
    logger.info("invitation_accepted", workspace_id=invitation.workspace_id, user=actor.user_id)
    return invitation.workspace_id


# --- GitHub installations ---------------------------------------------------------------------


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def user_installations(
    github_api: httpx.AsyncClient, user_token: str, app_id: int
) -> list[dict[str, Any]]:
    """Installations of *this* App that the signed-in user can access, per GitHub."""
    found: list[dict[str, Any]] = []
    for page in range(1, MAX_INSTALLATION_PAGES + 1):
        try:
            response = await github_api.get(
                "/user/installations",
                params={"per_page": 100, "page": page},
                headers=_github_headers(user_token),
                follow_redirects=False,
            )
            if response.status_code == 401:
                raise WorkspaceError("github_reauthorization_required", 401)
            response.raise_for_status()
            items = response.json().get("installations", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            raise WorkspaceError("github_unavailable", 502) from None
        for item in items:
            account = item.get("account") or {}
            if (
                item.get("app_id") == app_id
                and type(item.get("id")) is int
                and item["id"] > 0
                and type(account.get("id")) is int
                and isinstance(account.get("login"), str)
            ):
                found.append(
                    {
                        "installation_id": item["id"],
                        "account_id": account["id"],
                        "account_login": account["login"],
                        "account_type": str(account.get("type") or "User"),
                        "suspended": item.get("suspended_at") is not None,
                    }
                )
        if len(items) < 100:
            break
    return found


async def org_membership(
    github_api: httpx.AsyncClient, user_token: str, org_login: str
) -> dict[str, Any] | None:
    """The signed-in user's membership of an organization, as GitHub reports it to their own
    token, or None if GitHub will not confirm it (not a member, or the App lacks the
    organization "Members: read" permission). Callers fail closed on None."""
    try:
        response = await github_api.get(
            f"/user/memberships/orgs/{org_login}",
            headers=_github_headers(user_token),
            follow_redirects=False,
        )
        if response.status_code in (403, 404):
            return None
        if response.status_code == 401:
            raise WorkspaceError("github_reauthorization_required", 401)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise WorkspaceError("github_unavailable", 502) from None
    return data if isinstance(data, dict) else None


async def installation_repositories(
    github_api: httpx.AsyncClient, installation_token: str
) -> list[dict[str, Any]]:
    """Repositories of an installation, read with an App-issued installation token."""
    repositories: list[dict[str, Any]] = []
    for page in range(1, MAX_REPOSITORY_PAGES + 1):
        try:
            response = await github_api.get(
                "/installation/repositories",
                params={"per_page": 100, "page": page},
                headers=_github_headers(installation_token),
                follow_redirects=False,
            )
            response.raise_for_status()
            items = response.json().get("repositories", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            raise WorkspaceError("github_unavailable", 502) from None
        repositories.extend(
            r
            for r in items
            if type(r.get("id")) is int and r["id"] > 0 and isinstance(r.get("full_name"), str)
        )
        if len(items) < 100:
            break
    return repositories


def authorize_installation_link(
    *,
    workspace_kind: str,
    actor: CurrentUser,
    installation: dict[str, Any],
    membership: dict[str, Any] | None,
) -> None:
    """Who may attach an installation to a workspace (ADR-0012 §4). Visibility is not authority:
    GET /user/installations also lists installations the user can merely read.

    * A user-account installation: only that GitHub user, into any workspace they administer.
    * An organization installation: only a verified, active organization owner (GitHub role
      "admin"), and only into a team workspace. A normal member never can.
    * Any other account type is refused.
    """
    if installation["suspended"]:
        raise WorkspaceError("installation_suspended", 409)
    account_type = installation["account_type"]
    if account_type == "User":
        if installation["account_id"] != actor.github_user_id:
            raise WorkspaceError("installation_account_not_yours", 403)
        return
    if account_type != "Organization":
        raise WorkspaceError("installation_account_unsupported", 403)
    if workspace_kind != "team":
        raise WorkspaceError("personal_workspace_own_account_only", 403)
    organization = (membership or {}).get("organization") or {}
    user = (membership or {}).get("user") or {}
    if (
        membership is None
        or membership.get("state") != "active"
        or membership.get("role") != "admin"
        or organization.get("id") != installation["account_id"]
        or user.get("id") != actor.github_user_id
    ):
        raise WorkspaceError("org_owner_required", 403)


def verify_installation_ownership(
    installation: dict[str, Any], repositories: list[dict[str, Any]]
) -> None:
    """The App-issued token's repositories must exist and all belong to the account the user's
    view reported, so the installation ID is bound to that account and this App."""
    if not repositories:
        raise WorkspaceError("installation_has_no_repositories", 409)
    if any((r.get("owner") or {}).get("id") != installation["account_id"] for r in repositories):
        raise WorkspaceError("installation_ownership_mismatch", 409)


async def workspace_kind(db: AsyncSession, scope: WorkspaceScope) -> str:
    return (await _workspace(db, scope)).kind


async def link_installation(
    db: AsyncSession,
    scope: WorkspaceScope,
    actor: CurrentUser,
    installation: dict[str, Any],
    membership: dict[str, Any] | None,
    repositories: list[dict[str, Any]],
) -> dict[str, Any]:
    """Authorize, verify and record an installation with its repositories in one transaction.

    `installation` is the entry from the user's own GET /user/installations (so the user can at
    least see it), `membership` their verified organization membership (organizations only), and
    `repositories` what an App-issued installation token lists. An installation belongs to at
    most one workspace: reuse requires the current workspace to unlink it first."""
    if not can_manage_members(scope.role):
        raise _forbidden()
    authorize_installation_link(
        workspace_kind=(await _workspace(db, scope)).kind,
        actor=actor,
        installation=installation,
        membership=membership,
    )
    verify_installation_ownership(installation, repositories)
    installation_id: int = installation["installation_id"]
    now = datetime.now(UTC)
    db.add(
        GitHubInstallationModel(
            installation_id=installation_id,
            workspace_id=scope.workspace_id,
            account_id=installation["account_id"],
            account_login=installation["account_login"],
            account_type=installation["account_type"][:16],
            linked_by=actor.user_id,
            linked_at=now,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceError("installation_already_linked", 409) from None
    await _store_repositories(db, installation_id, repositories, now)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise WorkspaceError("installation_already_linked", 409) from None
    logger.info(
        "installation_linked",
        workspace_id=scope.workspace_id,
        installation_id=installation_id,
        account_type=installation["account_type"],
        by=actor.user_id,
    )
    return installation


async def _store_repositories(
    db: AsyncSession, installation_id: int, repositories: list[dict[str, Any]], now: datetime
) -> None:
    await db.execute(
        delete(WorkspaceRepositoryModel).where(
            WorkspaceRepositoryModel.installation_id == installation_id,
            WorkspaceRepositoryModel.github_repository_id.notin_([r["id"] for r in repositories]),
        )
    )
    for repo in repositories:
        await db.execute(
            insert(WorkspaceRepositoryModel)
            .values(
                github_repository_id=repo["id"],
                installation_id=installation_id,
                full_name=repo["full_name"][:255],
                private=bool(repo.get("private", True)),
                synced_at=now,
            )
            .on_conflict_do_update(
                index_elements=[WorkspaceRepositoryModel.github_repository_id],
                # Only within this installation: a repository is never moved between workspaces.
                set_={"full_name": repo["full_name"][:255], "synced_at": now},
                where=WorkspaceRepositoryModel.installation_id == installation_id,
            )
        )


async def linked_installation(
    db: AsyncSession, scope: WorkspaceScope, installation_id: int
) -> GitHubInstallationModel | None:
    """The installation if, and only if, this workspace links it."""
    found: GitHubInstallationModel | None = (
        await db.execute(
            select(GitHubInstallationModel).where(
                GitHubInstallationModel.installation_id == installation_id,
                GitHubInstallationModel.workspace_id == scope.workspace_id,
            )
        )
    ).scalar_one_or_none()
    return found


async def sync_repositories(
    db: AsyncSession,
    scope: WorkspaceScope,
    installation_id: int,
    repositories: list[dict[str, Any]],
    account_id: int | None = None,
) -> int:
    """Refresh the stored repositories of an installation this workspace has linked."""
    if not can_manage_members(scope.role):
        raise _forbidden()
    linked = await linked_installation(db, scope, installation_id)
    if linked is None:
        raise WorkspaceError("installation_not_found", 404)
    verify_installation_ownership(
        {"account_id": account_id if account_id is not None else linked.account_id}, repositories
    )
    await _store_repositories(db, installation_id, repositories, datetime.now(UTC))
    await db.commit()
    return len(repositories)


async def unlink_installation(
    db: AsyncSession, scope: WorkspaceScope, installation_id: int
) -> None:
    """Unlink; its pipelines and repositories are invisible to the workspace immediately."""
    if not can_manage_members(scope.role):
        raise _forbidden()
    result = await db.execute(
        delete(GitHubInstallationModel)
        .where(
            GitHubInstallationModel.installation_id == installation_id,
            GitHubInstallationModel.workspace_id == scope.workspace_id,
        )
        .returning(GitHubInstallationModel.installation_id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise WorkspaceError("installation_not_found", 404)
    await db.commit()


async def list_installations(db: AsyncSession, scope: WorkspaceScope) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(GitHubInstallationModel).where(
                GitHubInstallationModel.workspace_id == scope.workspace_id
            )
        )
    ).scalars()
    return [
        {
            "installation_id": row.installation_id,
            "account_login": row.account_login,
            "account_type": row.account_type,
            "linked_at": row.linked_at,
        }
        for row in rows
    ]


async def list_repositories(db: AsyncSession, scope: WorkspaceScope) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(WorkspaceRepositoryModel)
            .where(WorkspaceRepositoryModel.installation_id.in_(scope.installation_ids()))
            .order_by(WorkspaceRepositoryModel.full_name)
        )
    ).scalars()
    return [
        {
            "github_repository_id": row.github_repository_id,
            "full_name": row.full_name,
            "private": row.private,
            "installation_id": row.installation_id,
        }
        for row in rows
    ]


# --- Approval authorization -----------------------------------------------------------------


async def workspace_approver_authorized(
    db: AsyncSession, approval_id: str, slack_user_id: str
) -> bool:
    """Is this Slack user an owner-attested approver in the workspace that owns the approval's
    pipeline? Default deny. True only if all hold, read fresh from PostgreSQL:

    * the pipeline has a verified installation linked to a workspace;
    * a member of that workspace is mapped to this Slack user, with an attester and a time;
    * the attester is still an OWNER of that same workspace;
    * the member's current role may approve (APPROVER or higher).

    The global Slack allowlist is checked separately by both callers. Called when the decision is
    recorded and again when the remediation job runs, so a revoked mapping, a removed or demoted
    member, a demoted attester or an unlinked installation stops a pending approval."""
    if not slack_user_id:
        return False
    attester = aliased(WorkspaceMembershipModel)
    role = (
        await db.execute(
            select(WorkspaceMembershipModel.role)
            .join(
                GitHubInstallationModel,
                GitHubInstallationModel.workspace_id == WorkspaceMembershipModel.workspace_id,
            )
            .join(
                PipelineModel,
                PipelineModel.installation_id == GitHubInstallationModel.installation_id,
            )
            .join(
                attester,
                and_(
                    attester.workspace_id == WorkspaceMembershipModel.workspace_id,
                    attester.user_id == WorkspaceMembershipModel.slack_mapped_by,
                    attester.role == Role.OWNER.value,
                ),
            )
            .where(
                PipelineModel.approval_id == approval_id,
                WorkspaceMembershipModel.slack_user_id == slack_user_id,
                WorkspaceMembershipModel.slack_mapped_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    return role is not None and can_approve(Role(role))
