"""GitHub sign-in, sessions and the request dependencies every tenant route uses (ADR-0012).

- `current_user` resolves the HttpOnly session cookie against PostgreSQL on every request.
- `require_csrf` guards every state-changing browser request: the custom `X-Akesis-CSRF` header
  cannot be sent cross-site without a CORS preflight, and a present `Origin` must be allowed.
- `workspace_scope` turns a path workspace ID into a `WorkspaceScope` only after a fresh
  membership check. A workspace ID is an identifier, never a credential: non-members get 404.
"""

from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.session import get_db_session
from src.packages.database.tenancy import WorkspaceAccessDenied, WorkspaceScope, authorize
from src.packages.shared import auth_service, workspace_service
from src.packages.shared.auth_service import (
    BINDING_COOKIE,
    BINDING_COOKIE_PATH,
    SESSION_COOKIE,
    AuthError,
    CurrentUser,
)
from src.packages.shared.config import Settings
from src.packages.shared.workspace_service import WorkspaceError

router = APIRouter(tags=["Auth"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
GITHUB_API = "https://api.github.com"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _settings(request: Request) -> Settings:
    active: Settings = request.app.state.settings
    return active


def auth_http_error(error: AuthError | WorkspaceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.code)


async def require_csrf(request: Request) -> None:
    if request.method not in _UNSAFE_METHODS:
        return
    if request.headers.get("X-Akesis-CSRF") != "1":
        raise HTTPException(status_code=403, detail="csrf_check_failed")
    origin = request.headers.get("Origin")
    if origin is not None and origin not in _settings(request).cors_allowed_origins:
        raise HTTPException(status_code=403, detail="csrf_check_failed")


async def current_user(request: Request, db: DbSession) -> CurrentUser:
    try:
        return await auth_service.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    except AuthError as error:
        raise auth_http_error(error) from None


SignedIn = Annotated[CurrentUser, Depends(current_user)]


async def workspace_scope(workspace_id: str, user: SignedIn, db: DbSession) -> WorkspaceScope:
    try:
        return await authorize(db, user_id=user.user_id, workspace_id=workspace_id)
    except WorkspaceAccessDenied as denied:
        if denied.is_member:
            raise HTTPException(status_code=403, detail="insufficient_role") from None
        raise HTTPException(status_code=404, detail="workspace_not_found") from None


Scope = Annotated[WorkspaceScope, Depends(workspace_scope)]


def _cookie(response: Response, settings: Settings, name: str, value: str, **kwargs: Any) -> None:
    response.set_cookie(
        name, value, httponly=True, secure=settings.auth_cookie_secure, samesite="lax", **kwargs
    )


# --- Routes ---------------------------------------------------------------------------------


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=1, max_length=512)


class CallbackResponse(BaseModel):
    return_to: str


class WorkspaceView(BaseModel):
    workspace_id: str
    kind: str
    name: str
    slug: str
    role: str


class MeResponse(BaseModel):
    user_id: str
    github_login: str
    display_name: str | None
    avatar_url: str | None
    session_expires_at: datetime
    workspaces: list[WorkspaceView]


@router.get("/v1/auth/github/login", summary="Start GitHub sign-in (redirects to GitHub)")
async def github_login(
    request: Request,
    db: DbSession,
    return_to: Annotated[str | None, Query(max_length=512)] = None,
) -> RedirectResponse:
    settings = _settings(request)
    try:
        start = await auth_service.start_login(db, settings, return_to=return_to)
    except AuthError as error:
        return RedirectResponse(f"/login?error={error.code}", status_code=303)
    response = RedirectResponse(start.authorize_url, status_code=303)
    _cookie(
        response,
        settings,
        BINDING_COOKIE,
        start.browser_binding,
        max_age=int(auth_service.LOGIN_ATTEMPT_TTL.total_seconds()),
        path=BINDING_COOKIE_PATH,
    )
    return response


@router.post(
    "/v1/auth/github/callback",
    response_model=CallbackResponse,
    dependencies=[Depends(require_csrf)],
    summary="Complete GitHub sign-in with the code and state GitHub returned",
)
async def github_callback(
    body: CallbackRequest, request: Request, response: Response, db: DbSession
) -> CallbackResponse:
    settings = _settings(request)
    # The binding cookie is single use whatever the outcome.
    response.delete_cookie(BINDING_COOKIE, path=BINDING_COOKIE_PATH)
    try:
        async with httpx.AsyncClient(base_url=GITHUB_API, timeout=15.0) as github_api:
            issued = await auth_service.complete_login(
                db,
                settings,
                code=body.code,
                state=body.state,
                browser_binding=request.cookies.get(BINDING_COOKIE),
                github_api=github_api,
            )
    except AuthError as error:
        raise auth_http_error(error) from None
    _cookie(
        response,
        settings,
        SESSION_COOKIE,
        issued.token,
        max_age=settings.auth_session_ttl_hours * 3600,
        path="/",
    )
    return CallbackResponse(return_to=issued.return_to)


@router.post(
    "/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    summary="Sign out and revoke this session",
)
async def logout(request: Request, db: DbSession) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    try:
        user = await auth_service.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    except AuthError:
        return response  # already signed out: logout is idempotent
    await auth_service.revoke_session(db, user.session_id)
    return response


@router.post(
    "/v1/auth/sessions/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
    summary="Sign out everywhere: revoke every session of the signed-in user",
)
async def revoke_all(user: SignedIn, db: DbSession) -> Response:
    await auth_service.revoke_all_sessions(db, user.user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/v1/me", response_model=MeResponse, summary="The signed-in user and workspaces")
async def me(user: SignedIn, db: DbSession) -> MeResponse:
    workspaces = await workspace_service.list_my_workspaces(db, user.user_id)
    return MeResponse(
        user_id=user.user_id,
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        session_expires_at=user.session_expires_at,
        workspaces=[WorkspaceView(**vars(ws)) for ws in workspaces],
    )
