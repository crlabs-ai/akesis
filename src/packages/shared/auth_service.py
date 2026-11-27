"""GitHub sign-in and server-side sessions (Phase 2, ADR-0012).

Sign-in uses the Akesis GitHub App's user authorization: OAuth 2.0 authorization code with PKCE
(S256), through Authlib. Every credential stays server-side:

- The browser receives only an opaque, random session token in an HttpOnly cookie. PostgreSQL
  stores its SHA-256, so a database read does not yield usable sessions.
- The GitHub user-to-server token is encrypted at rest (Fernet) on the session row and is used
  only to check which App installations the user can access. It never reaches browser code.
- `state` and the PKCE verifier are stored server-side, are single use, expire after ten minutes,
  and are bound to the browser that started sign-in by a separate HttpOnly binding cookie. That
  stops login CSRF and authorization-code injection.
- Users are identified by their immutable numeric GitHub user ID, never by login.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import (
    AuthSessionModel,
    OAuthLoginAttemptModel,
    UserModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.packages.shared.config import Settings
from src.packages.shared.logging import get_logger
from src.packages.shared.workspace_roles import Role

logger = get_logger("akesis.auth")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
LOGIN_ATTEMPT_TTL = timedelta(minutes=10)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)

SESSION_COOKIE = "akesis_session"
BINDING_COOKIE = "akesis_oauth"
BINDING_COOKIE_PATH = "/v1/auth"

_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{16,512}$")
_RETURN_TO = re.compile(r"^/app(?:/[A-Za-z0-9._~/-]*)?$")
DEFAULT_RETURN_TO = "/app"


class AuthError(Exception):
    """A sign-in or session failure. `code` is safe to show; nothing secret is ever included."""

    def __init__(self, code: str, status_code: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class LoginStart:
    authorize_url: str
    browser_binding: str


@dataclass(frozen=True)
class IssuedSession:
    token: str
    expires_at: datetime
    user_id: str
    return_to: str


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    session_id: str
    github_user_id: int
    github_login: str
    display_name: str | None
    avatar_url: str | None
    session_expires_at: datetime


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def safe_return_to(value: str | None) -> str:
    """Only same-site console paths; anything else (absolute URLs, //host, ..) goes to /app."""
    if not value or ".." in value or not _RETURN_TO.fullmatch(value):
        return DEFAULT_RETURN_TO
    return value


def sign_in_configured(settings: Settings) -> bool:
    return bool(settings.github_app_client_id.strip() and settings.github_app_client_secret.strip())


def _oauth_client(settings: Settings, **kwargs: Any) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.github_app_client_id,
        client_secret=settings.github_app_client_secret,
        redirect_uri=settings.auth_redirect_uri,
        code_challenge_method="S256",
        token_endpoint_auth_method="client_secret_post",
        timeout=15.0,
        **kwargs,
    )


def _fernet(settings: Settings) -> Fernet | None:
    if not settings.auth_token_encryption_key:
        return None
    try:
        return Fernet(settings.auth_token_encryption_key.encode())
    except (ValueError, TypeError):
        return None


# --- Sign-in --------------------------------------------------------------------------------


async def start_login(db: AsyncSession, settings: Settings, *, return_to: str | None) -> LoginStart:
    """Create a single-use sign-in attempt and the GitHub authorization URL."""
    if not sign_in_configured(settings):
        raise AuthError("sign_in_not_configured", 503)
    state = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 chars; RFC 7636 allows 43-128
    client = _oauth_client(settings)
    try:
        url, _ = client.create_authorization_url(
            GITHUB_AUTHORIZE_URL, state=state, code_verifier=verifier
        )
    finally:
        await client.aclose()
    now = datetime.now(UTC)
    db.add(
        OAuthLoginAttemptModel(
            attempt_id=new_id("oauth"),
            state_sha256=digest(state),
            browser_binding_sha256=digest(binding),
            code_verifier=verifier,
            return_to=safe_return_to(return_to),
            created_at=now,
            expires_at=now + LOGIN_ATTEMPT_TTL,
        )
    )
    # Housekeeping: spent or abandoned attempts are useless after a day.
    await db.execute(
        delete(OAuthLoginAttemptModel).where(
            OAuthLoginAttemptModel.expires_at < now - timedelta(days=1)
        )
    )
    await db.commit()
    return LoginStart(authorize_url=url, browser_binding=binding)


async def complete_login(
    db: AsyncSession,
    settings: Settings,
    *,
    code: str,
    state: str,
    browser_binding: str | None,
    github_api: httpx.AsyncClient,
) -> IssuedSession:
    """Validate the callback, exchange the code (PKCE), map the identity and open a session."""
    if not sign_in_configured(settings):
        raise AuthError("sign_in_not_configured", 503)
    if not _OPAQUE.fullmatch(code or "") or not _OPAQUE.fullmatch(state or ""):
        raise AuthError("invalid_callback", 400)
    if not browser_binding:
        raise AuthError("invalid_state", 400)

    now = datetime.now(UTC)
    # Consume the attempt first and commit, so the state is single use even if the exchange
    # below fails or is retried.
    attempt = (
        await db.execute(
            select(OAuthLoginAttemptModel)
            .where(OAuthLoginAttemptModel.state_sha256 == digest(state))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        attempt is None
        or attempt.consumed_at is not None
        or attempt.expires_at <= now
        or not hmac.compare_digest(attempt.browser_binding_sha256, digest(browser_binding))
    ):
        await db.rollback()
        logger.warning("sign_in_rejected_invalid_state")
        raise AuthError("invalid_state", 400)
    attempt.consumed_at = now
    verifier, return_to = attempt.code_verifier, attempt.return_to
    await db.commit()

    token = await _exchange_code(settings, code=code, verifier=verifier)
    profile = await _github_profile(github_api, token["access_token"])
    user_id = await provision_user(db, profile, now=now)
    issued = await _issue_session(db, settings, user_id=user_id, github_token=token, now=now)
    logger.info("sign_in_completed", user_id=user_id, github_user_id=profile["id"])
    return IssuedSession(issued[0], issued[1], user_id, return_to)


async def _exchange_code(settings: Settings, *, code: str, verifier: str) -> dict[str, Any]:
    client = _oauth_client(settings)
    try:
        token = await client.fetch_token(
            GITHUB_TOKEN_URL, code=code, code_verifier=verifier, grant_type="authorization_code"
        )
    except Exception:
        # Authlib raises OAuthError for GitHub's error responses (bad_verification_code, expired
        # or reused code, PKCE mismatch); never echo provider detail or credentials.
        logger.warning("sign_in_code_exchange_failed")
        raise AuthError("code_exchange_failed", 400) from None
    finally:
        await client.aclose()
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise AuthError("code_exchange_failed", 400)
    return dict(token)


async def _github_profile(github_api: httpx.AsyncClient, access_token: str) -> dict[str, Any]:
    try:
        response = await github_api.get(
            "/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise AuthError("github_profile_unavailable", 502) from None
    user_id, login = data.get("id"), data.get("login")
    if (
        type(user_id) is not int
        or user_id <= 0
        or not isinstance(login, str)
        or not _GITHUB_LOGIN.fullmatch(login)
    ):
        raise AuthError("github_profile_invalid", 502)
    name = data.get("name") if isinstance(data.get("name"), str) else None
    avatar = data.get("avatar_url")
    avatar = avatar if isinstance(avatar, str) and avatar.startswith("https://") else None
    return {"id": user_id, "login": login, "name": (name or None) and name[:255], "avatar": avatar}


async def provision_user(db: AsyncSession, profile: dict[str, Any], *, now: datetime) -> str:
    """Upsert the user by GitHub ID and guarantee exactly one personal workspace.

    Safe under repeated and concurrent callbacks: every step is an idempotent upsert on a unique
    key (github_user_id, workspaces.personal_owner_id, the membership primary key).
    """
    user_id = (
        await db.execute(
            insert(UserModel)
            .values(
                user_id=new_id("usr"),
                github_user_id=profile["id"],
                github_login=profile["login"],
                display_name=profile["name"],
                avatar_url=profile["avatar"],
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            .on_conflict_do_update(
                index_elements=[UserModel.github_user_id],
                set_={
                    "github_login": profile["login"],
                    "display_name": profile["name"],
                    "avatar_url": profile["avatar"],
                    "updated_at": now,
                    "last_login_at": now,
                },
            )
            .returning(UserModel.user_id)
        )
    ).scalar_one()
    await db.execute(
        insert(WorkspaceModel)
        .values(
            workspace_id=new_id("ws"),
            kind="personal",
            name="Personal",
            slug=f"personal-{profile['id']}",
            personal_owner_id=user_id,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[WorkspaceModel.personal_owner_id])
    )
    workspace_id = (
        await db.execute(
            select(WorkspaceModel.workspace_id).where(WorkspaceModel.personal_owner_id == user_id)
        )
    ).scalar_one()
    await db.execute(
        insert(WorkspaceMembershipModel)
        .values(
            workspace_id=workspace_id,
            user_id=user_id,
            role=Role.OWNER.value,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
    )
    await db.commit()
    return user_id


# --- Sessions -------------------------------------------------------------------------------


async def _issue_session(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    github_token: dict[str, Any],
    now: datetime,
) -> tuple[str, datetime]:
    session_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=settings.auth_session_ttl_hours)
    ciphertext: str | None = None
    token_expires_at: datetime | None = None
    fernet = _fernet(settings)
    if fernet is not None:
        ciphertext = fernet.encrypt(github_token["access_token"].encode()).decode()
        expires_in = github_token.get("expires_in")
        token_expires_at = (
            now + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int | float) and expires_in > 0
            else expires_at
        )
    db.add(
        AuthSessionModel(
            session_id=new_id("ses"),
            token_sha256=digest(session_token),
            user_id=user_id,
            github_token_ciphertext=ciphertext,
            github_token_expires_at=token_expires_at,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
        )
    )
    await db.commit()
    return session_token, expires_at


async def resolve_session(db: AsyncSession, token: str | None) -> CurrentUser:
    """The signed-in user for a session cookie, or AuthError. Checked against the database on
    every request, so revocation and expiry take effect immediately."""
    if not token or not _OPAQUE.fullmatch(token):
        raise AuthError("not_signed_in", 401)
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(AuthSessionModel, UserModel)
            .join(UserModel, UserModel.user_id == AuthSessionModel.user_id)
            .where(AuthSessionModel.token_sha256 == digest(token))
        )
    ).one_or_none()
    if row is None:
        raise AuthError("not_signed_in", 401)
    auth_session, user = row
    if auth_session.revoked_at is not None:
        raise AuthError("session_revoked", 401)
    if auth_session.expires_at <= now:
        raise AuthError("session_expired", 401)
    if (
        auth_session.last_seen_at is None
        or now - auth_session.last_seen_at > SESSION_TOUCH_INTERVAL
    ):
        await db.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.session_id == auth_session.session_id)
            .values(last_seen_at=now)
        )
        await db.commit()
    return CurrentUser(
        user_id=user.user_id,
        session_id=auth_session.session_id,
        github_user_id=user.github_user_id,
        github_login=user.github_login,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        session_expires_at=auth_session.expires_at,
    )


async def revoke_session(db: AsyncSession, session_id: str) -> None:
    await db.execute(
        update(AuthSessionModel)
        .where(AuthSessionModel.session_id == session_id, AuthSessionModel.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), github_token_ciphertext=None)
    )
    await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        update(AuthSessionModel)
        .where(AuthSessionModel.user_id == user_id, AuthSessionModel.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), github_token_ciphertext=None)
        .returning(AuthSessionModel.session_id)
    )
    revoked = len(result.all())
    await db.commit()
    return revoked


async def github_user_token(db: AsyncSession, settings: Settings, session_id: str) -> str:
    """Decrypt this session's GitHub token for a server-side call, or ask for re-authorization."""
    row = (
        await db.execute(
            select(
                AuthSessionModel.github_token_ciphertext,
                AuthSessionModel.github_token_expires_at,
            ).where(AuthSessionModel.session_id == session_id)
        )
    ).one_or_none()
    fernet = _fernet(settings)
    if fernet is None:
        raise AuthError("token_encryption_not_configured", 503)
    if row is None or row[0] is None or row[1] is None or row[1] <= datetime.now(UTC):
        raise AuthError("github_reauthorization_required", 401)
    try:
        return fernet.decrypt(row[0].encode()).decode()
    except InvalidToken:
        raise AuthError("github_reauthorization_required", 401) from None
