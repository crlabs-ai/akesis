"""Workspace, membership, invitation and installation routes (ADR-0012).

Every route under /v1/workspaces/{workspace_id} takes its `WorkspaceScope` from a fresh
membership check; role rules live in `workspace_service`, not here.
"""

from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from src.apps.api.auth import (
    GITHUB_API,
    DbSession,
    Scope,
    SignedIn,
    WorkspaceView,
    auth_http_error,
    require_csrf,
)
from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError
from src.packages.shared import auth_service, workspace_service
from src.packages.shared.auth_service import AuthError
from src.packages.shared.config import Settings
from src.packages.shared.workspace_roles import Role, can_assign, can_manage_members
from src.packages.shared.workspace_service import WorkspaceError

router = APIRouter(tags=["Workspaces"], dependencies=[Depends(require_csrf)])
WS = "/v1/workspaces/{workspace_id}"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameBody(_Body):
    name: str = Field(min_length=1, max_length=200)


class RoleBody(_Body):
    role: Role


class SlackBody(_Body):
    slack_user_id: str | None = Field(default=None, max_length=32)


class InviteBody(_Body):
    github_login: str = Field(min_length=1, max_length=40)
    role: Role


class InstallationBody(_Body):
    installation_id: int = Field(gt=0)


class MemberView(BaseModel):
    user_id: str
    github_login: str
    display_name: str | None
    avatar_url: str | None
    role: str
    slack_user_id: str | None
    # Who attested the Slack mapping, and when (owners and admins only; null otherwise).
    slack_mapped_by_login: str | None = None
    slack_mapped_at: datetime | None = None
    joined_at: datetime


class InvitationView(BaseModel):
    invitation_id: str
    github_user_id: int  # the immutable target; github_login is display only
    github_login: str
    avatar_url: str | None = None
    role: str
    created_at: datetime
    expires_at: datetime
    workspace_id: str | None = None
    workspace_name: str | None = None


class InstallationView(BaseModel):
    installation_id: int
    account_login: str
    account_type: str
    linked_at: datetime | None = None
    suspended: bool | None = None


class RepositoryView(BaseModel):
    github_repository_id: int
    full_name: str
    private: bool
    installation_id: int


def _settings(request: Request) -> Settings:
    active: Settings = request.app.state.settings
    return active


async def _call(awaitable: Any) -> Any:
    try:
        return await awaitable
    except (WorkspaceError, AuthError) as error:
        raise auth_http_error(error) from None


# --- Workspaces -----------------------------------------------------------------------------


@router.get("/v1/workspaces", response_model=list[WorkspaceView])
async def list_workspaces(user: SignedIn, db: DbSession) -> list[WorkspaceView]:
    return [
        WorkspaceView(**vars(ws))
        for ws in await workspace_service.list_my_workspaces(db, user.user_id)
    ]


@router.post("/v1/workspaces", response_model=WorkspaceView, status_code=201)
async def create_workspace(body: NameBody, user: SignedIn, db: DbSession) -> WorkspaceView:
    ws = await _call(workspace_service.create_team_workspace(db, user, body.name))
    return WorkspaceView(**vars(ws))


@router.patch(WS, status_code=204)
async def rename_workspace(body: NameBody, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.rename_workspace(db, scope, body.name))
    return Response(status_code=204)


@router.post(f"{WS}/leave", status_code=204)
async def leave_workspace(scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.leave_workspace(db, scope))
    return Response(status_code=204)


# --- Members --------------------------------------------------------------------------------


@router.get(f"{WS}/members", response_model=list[MemberView])
async def list_members(scope: Scope, db: DbSession) -> list[MemberView]:
    return [MemberView(**m) for m in await workspace_service.list_members(db, scope)]


@router.patch(f"{WS}/members/{{user_id}}", status_code=204)
async def change_role(user_id: str, body: RoleBody, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.change_role(db, scope, user_id, body.role))
    return Response(status_code=204)


@router.put(f"{WS}/members/{{user_id}}/slack", status_code=204)
async def set_slack_user(user_id: str, body: SlackBody, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.set_slack_user(db, scope, user_id, body.slack_user_id))
    return Response(status_code=204)


@router.delete(f"{WS}/members/{{user_id}}", status_code=204)
async def remove_member(user_id: str, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.remove_member(db, scope, user_id))
    return Response(status_code=204)


# --- Invitations ----------------------------------------------------------------------------


@router.get(f"{WS}/invitations", response_model=list[InvitationView])
async def list_invitations(scope: Scope, db: DbSession) -> list[InvitationView]:
    rows = await _call(workspace_service.list_invitations(db, scope))
    return [InvitationView(**row) for row in rows]


@router.post(f"{WS}/invitations", response_model=InvitationView, status_code=201)
async def invite(
    body: InviteBody, request: Request, scope: Scope, user: SignedIn, db: DbSession
) -> InvitationView:
    """Resolve the username to GitHub's numeric account ID now; the invitation is bound to it."""
    if not can_assign(scope.role, body.role):
        raise auth_http_error(WorkspaceError("insufficient_role", 403))
    token = await _call(auth_service.github_user_token(db, _settings(request), user.session_id))
    async with httpx.AsyncClient(base_url=GITHUB_API, timeout=15.0) as api:
        target = await _call(workspace_service.resolve_github_user(api, token, body.github_login))
    row = await _call(workspace_service.invite(db, scope, target, body.role))
    return InvitationView(**row)


@router.delete(f"{WS}/invitations/{{invitation_id}}", status_code=204)
async def revoke_invitation(invitation_id: str, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.revoke_invitation(db, scope, invitation_id))
    return Response(status_code=204)


@router.get("/v1/me/invitations", response_model=list[InvitationView])
async def my_invitations(user: SignedIn, db: DbSession) -> list[InvitationView]:
    return [InvitationView(**row) for row in await workspace_service.my_invitations(db, user)]


@router.post("/v1/me/invitations/{invitation_id}/accept", response_model=WorkspaceView)
async def accept_invitation(invitation_id: str, user: SignedIn, db: DbSession) -> WorkspaceView:
    workspace_id = await _call(workspace_service.accept_invitation(db, user, invitation_id))
    mine = await workspace_service.list_my_workspaces(db, user.user_id)
    return next(WorkspaceView(**vars(ws)) for ws in mine if ws.workspace_id == workspace_id)


# --- Installations and repositories ---------------------------------------------------------


async def _accessible(request: Request, user: SignedIn, db: DbSession) -> list[dict[str, Any]]:
    settings = _settings(request)
    token = await _call(auth_service.github_user_token(db, settings, user.session_id))
    async with httpx.AsyncClient(base_url=GITHUB_API, timeout=15.0) as api:
        rows: list[dict[str, Any]] = await _call(
            workspace_service.user_installations(api, token, settings.github_app_id)
        )
    return rows


async def _repositories(request: Request, installation_id: int) -> list[dict[str, Any]]:
    """Repositories listed by an App-issued, metadata-only installation token. Issuing it
    verifies with the App JWT that the installation belongs to this App and is not suspended."""
    settings = _settings(request)
    if settings.github_app_id <= 0 or not settings.github_app_private_key.strip():
        raise auth_http_error(WorkspaceError("github_app_not_configured", 503))
    async with httpx.AsyncClient(base_url=GITHUB_API, timeout=15.0) as api:
        try:
            token = await GitHubAppAuth(
                settings.github_app_id, settings.github_app_private_key, client=api
            ).installation_token(installation_id, permissions={"metadata": "read"})
        except GitHubAppAuthenticationError as error:
            code = "installation_suspended" if error.code == "installation_suspended" else None
            raise auth_http_error(
                WorkspaceError(code or "installation_not_verified", 409 if code else 502)
            ) from None
        rows: list[dict[str, Any]] = await _call(
            workspace_service.installation_repositories(api, token.token)
        )
    return rows


@router.get(f"{WS}/installations", response_model=list[InstallationView])
async def list_installations(scope: Scope, db: DbSession) -> list[InstallationView]:
    return [InstallationView(**r) for r in await workspace_service.list_installations(db, scope)]


@router.get(f"{WS}/installations/available", response_model=list[InstallationView])
async def available_installations(
    request: Request, scope: Scope, user: SignedIn, db: DbSession
) -> list[InstallationView]:
    """Installations of the Akesis App that this user can access on GitHub."""
    return [InstallationView(**r) for r in await _accessible(request, user, db)]


@router.post(f"{WS}/installations", response_model=InstallationView, status_code=201)
async def link_installation(
    body: InstallationBody, request: Request, scope: Scope, user: SignedIn, db: DbSession
) -> InstallationView:
    """Link an installation (ADR-0012 §4): the user must see it on GitHub, own it (user account)
    or be a verified organization owner (organization, team workspaces only), and the App must
    confirm it and its repositories. Authorization is checked before any token is issued."""
    if not can_manage_members(scope.role):
        raise auth_http_error(WorkspaceError("insufficient_role", 403))
    accessible = await _accessible(request, user, db)
    installation = next(
        (i for i in accessible if i["installation_id"] == body.installation_id), None
    )
    if installation is None:
        raise auth_http_error(WorkspaceError("installation_not_accessible", 403))
    membership = None
    if installation["account_type"] == "Organization":
        token = await _call(auth_service.github_user_token(db, _settings(request), user.session_id))
        async with httpx.AsyncClient(base_url=GITHUB_API, timeout=15.0) as api:
            membership = await _call(
                workspace_service.org_membership(api, token, installation["account_login"])
            )
    try:  # fail before issuing any installation token
        workspace_service.authorize_installation_link(
            workspace_kind=await workspace_service.workspace_kind(db, scope),
            actor=user,
            installation=installation,
            membership=membership,
        )
    except WorkspaceError as error:
        raise auth_http_error(error) from None
    repositories = await _repositories(request, body.installation_id)
    linked = await _call(
        workspace_service.link_installation(db, scope, user, installation, membership, repositories)
    )
    return InstallationView(**linked)


@router.post(f"{WS}/installations/{{installation_id}}/sync")
async def sync_installation(
    installation_id: int, request: Request, scope: Scope, db: DbSession
) -> dict[str, int]:
    """Re-list an installation's repositories. Authorization (OWNER/ADMIN) and the workspace's own
    link are checked first, so an installation ID of another workspace, or one that does not
    exist, gets the same 404 and causes no GitHub call at all."""
    if not can_manage_members(scope.role):
        raise auth_http_error(WorkspaceError("insufficient_role", 403))
    linked = await workspace_service.linked_installation(db, scope, installation_id)
    if linked is None:
        raise auth_http_error(WorkspaceError("installation_not_found", 404))
    repositories = await _repositories(request, installation_id)
    count = await _call(
        workspace_service.sync_repositories(db, scope, installation_id, repositories)
    )
    return {"repositories": count}


@router.delete(f"{WS}/installations/{{installation_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_installation(installation_id: int, scope: Scope, db: DbSession) -> Response:
    await _call(workspace_service.unlink_installation(db, scope, installation_id))
    return Response(status_code=204)


@router.get(f"{WS}/repositories", response_model=list[RepositoryView])
async def list_repositories(scope: Scope, db: DbSession) -> list[RepositoryView]:
    return [RepositoryView(**r) for r in await workspace_service.list_repositories(db, scope)]
