"""Phase 2 (ADR-0012): GitHub sign-in, sessions, workspaces and tenant isolation.

Runs against the disposable PostgreSQL database. GitHub is mocked with respx; no real OAuth
credentials exist anywhere in this file (the client ID and secret below are synthetic).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.apps.api.main import create_app
from src.packages.database.models import (
    AuthSessionModel,
    GitHubInstallationModel,
    PipelineModel,
    UserModel,
    WorkspaceInvitationModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.packages.database.repositories import PipelineRepository
from src.packages.database.session import get_db_session
from src.packages.shared.auth_service import BINDING_COOKIE, SESSION_COOKIE
from src.packages.shared.config import Settings, settings
from src.packages.shared.models import PipelineRecord, PipelineStatus
from src.packages.shared.workspace_service import workspace_approver_authorized
from tests.support.tenancy import (
    CSRF,
    TEST_FERNET_KEY,
    Tenant,
    add_member,
    github_token_for,
    link_installation,
    reset_identity,
    seed_team,
    seed_user,
)

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
FERNET_KEY = TEST_FERNET_KEY  # synthetic
APP_ID = 4242


@pytest.fixture
async def db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await reset_identity(session)
        # Pipelines this file seeds: installation IDs 900000-999999 and these pipeline IDs.
        await session.execute(
            delete(PipelineModel).where(
                or_(
                    PipelineModel.installation_id.between(900_000, 999_999),
                    PipelineModel.pipeline_id.in_(("pipe_scoped", "pipe_inc_orphan")),
                    PipelineModel.pipeline_id.like("pipe_inc_b_%"),
                )
            )
        )
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.fixture(scope="module")
def app_private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def app(db: async_sessionmaker[AsyncSession], app_private_key: str) -> FastAPI:
    application = create_app(
        Settings(
            environment="test",
            github_app_id=APP_ID,
            github_app_private_key=app_private_key,
            github_app_client_id="Iv1.synthetic0000",
            github_app_client_secret="synthetic-client-secret",
            auth_token_encryption_key=FERNET_KEY,
            auth_cookie_secure=False,  # the test client speaks plain http
        )
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with db() as s:
            yield s

    application.dependency_overrides[get_db_session] = session
    return application


def _client(app: FastAPI, tenant: Tenant | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies=tenant.cookies if tenant else None,
    )


def _mock_github(github_id: int = 9001, login: str = "octo") -> None:
    respx.post(GITHUB_TOKEN_URL).respond(
        json={"access_token": "ghu_synthetic", "token_type": "bearer", "expires_in": 28800}
    )
    respx.get(GITHUB_USER_URL).respond(json={"id": github_id, "login": login, "name": "Octo"})


async def _start(client: httpx.AsyncClient, return_to: str = "/app/incidents") -> str:
    response = await client.get("/v1/auth/github/login", params={"return_to": return_to})
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://github.com/login/oauth/authorize"
    )
    query = parse_qs(location.query)
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    assert "code_verifier" not in query  # the verifier never leaves the server
    cookie = response.headers["set-cookie"]
    assert BINDING_COOKIE in cookie and "HttpOnly" in cookie and "Path=/v1/auth" in cookie
    return query["state"][0]


async def _sign_in(app: FastAPI, github_id: int = 9001, login: str = "octo") -> httpx.Response:
    async with _client(app) as client:
        state = await _start(client)
        with respx.mock:
            _mock_github(github_id, login)
            response = await client.post(
                "/v1/auth/github/callback",
                json={"code": "synthetic-code-0123456789", "state": state},
                headers=CSRF,
            )
    return response


async def _personal_workspaces(db: async_sessionmaker[AsyncSession], github_id: int) -> int:
    async with db() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(WorkspaceModel)
                .join(UserModel, UserModel.user_id == WorkspaceModel.personal_owner_id)
                .where(UserModel.github_user_id == github_id)
            )
        ).scalar_one()


# 1 and 5. Successful sign-in creates the user, one personal workspace and an HttpOnly session.
async def test_successful_github_sign_in(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    response = await _sign_in(app)
    assert response.status_code == 200
    assert response.json() == {"return_to": "/app/incidents"}
    cookie = next(c for c in response.headers.get_list("set-cookie") if SESSION_COOKIE in c)
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "ghu_synthetic" not in response.text and "ghu_synthetic" not in cookie
    token = response.cookies[SESSION_COOKIE]

    async with _client(app) as client:
        client.cookies.set(SESSION_COOKIE, token)
        me = (await client.get("/v1/me")).json()
    assert me["github_login"] == "octo"
    assert [(w["kind"], w["role"]) for w in me["workspaces"]] == [("personal", "owner")]
    assert "ghu_synthetic" not in str(me)
    assert await _personal_workspaces(db, 9001) == 1

    async with db() as s:  # the GitHub token is stored encrypted, the session token hashed
        row = (await s.execute(select(AuthSessionModel))).scalar_one()
    assert row.github_token_ciphertext and "ghu_synthetic" not in row.github_token_ciphertext
    assert row.token_sha256 != token


async def test_return_to_cannot_redirect_off_site(app: FastAPI) -> None:
    async with _client(app) as client:
        state = await _start(client, return_to="https://evil.example/app")
        with respx.mock:
            _mock_github()
            response = await client.post(
                "/v1/auth/github/callback",
                json={"code": "synthetic-code-0123456789", "state": state},
                headers=CSRF,
            )
    assert response.json() == {"return_to": "/app"}


# 2. Invalid OAuth state, wrong browser, replay.
async def test_invalid_state_is_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        await _start(client)
        with respx.mock(assert_all_called=False) as github:
            token_route = github.post(GITHUB_TOKEN_URL)
            response = await client.post(
                "/v1/auth/github/callback",
                json={"code": "synthetic-code-0123456789", "state": "forged-state-0123456789abc"},
                headers=CSRF,
            )
            assert not token_route.called  # no code exchange for an unknown state
    assert response.status_code == 400 and response.json() == {"detail": "invalid_state"}


async def test_state_from_another_browser_is_rejected(app: FastAPI) -> None:
    async with _client(app) as victim:
        state = await _start(victim)
    async with _client(app) as attacker:  # no binding cookie: login CSRF / code injection
        response = await attacker.post(
            "/v1/auth/github/callback",
            json={"code": "synthetic-code-0123456789", "state": state},
            headers=CSRF,
        )
    assert response.status_code == 400 and response.json() == {"detail": "invalid_state"}


async def test_state_is_single_use(app: FastAPI) -> None:
    async with _client(app) as client:
        state = await _start(client)
        binding = client.cookies[BINDING_COOKIE]
        with respx.mock:
            _mock_github()
            body = {"code": "synthetic-code-0123456789", "state": state}
            first = await client.post("/v1/auth/github/callback", json=body, headers=CSRF)
            client.cookies.set(BINDING_COOKIE, binding, path="/v1/auth")
            replay = await client.post("/v1/auth/github/callback", json=body, headers=CSRF)
    assert first.status_code == 200
    assert replay.status_code == 400 and replay.json() == {"detail": "invalid_state"}


async def test_cancelled_or_failed_code_exchange(app: FastAPI) -> None:
    async with _client(app) as client:
        state = await _start(client)
        with respx.mock:
            respx.post(GITHUB_TOKEN_URL).respond(
                json={"error": "bad_verification_code", "error_description": "secret detail"}
            )
            response = await client.post(
                "/v1/auth/github/callback",
                json={"code": "synthetic-code-0123456789", "state": state},
                headers=CSRF,
            )
    assert response.status_code == 400 and response.json() == {"detail": "code_exchange_failed"}
    assert SESSION_COOKIE not in response.cookies


async def test_callback_requires_csrf_header(app: FastAPI) -> None:
    async with _client(app) as client:
        state = await _start(client)
        response = await client.post(
            "/v1/auth/github/callback", json={"code": "synthetic-code-0123456789", "state": state}
        )
        assert response.status_code == 403
        foreign = await client.post(
            "/v1/auth/github/callback",
            json={"code": "synthetic-code-0123456789", "state": state},
            headers={**CSRF, "Origin": "https://evil.example"},
        )
        assert foreign.status_code == 403


async def test_sign_in_not_configured_is_reported(db: async_sessionmaker[AsyncSession]) -> None:
    unconfigured = create_app(Settings(environment="test", github_app_client_id=""))
    async with _client(unconfigured) as client:
        response = await client.get("/v1/auth/github/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=sign_in_not_configured"


# 3. Expired session.
async def test_expired_session_is_rejected(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        user = await seed_user(s, 9101)
        await s.execute(
            update(AuthSessionModel).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    async with _client(app, user) as client:
        response = await client.get("/v1/me")
    assert response.status_code == 401 and response.json() == {"detail": "session_expired"}


# 4. Logout and revoked sessions.
async def test_logout_revokes_the_session(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        user = await seed_user(s, 9201)
    async with _client(app, user) as client:
        assert (await client.post("/v1/auth/logout", headers=CSRF)).status_code == 204
    async with _client(app, user) as client:  # the old cookie value, replayed
        response = await client.get("/v1/me")
    assert response.status_code == 401 and response.json() == {"detail": "session_revoked"}


async def test_revoke_all_signs_out_every_session(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        first = await seed_user(s, 9301)
        second = await seed_user(s, 9301)  # same GitHub account, a second browser
    async with _client(app, first) as client:
        response = await client.post("/v1/auth/sessions/revoke-all", headers=CSRF)
        assert response.status_code == 204
    for tenant in (first, second):
        async with _client(app, tenant) as client:
            assert (await client.get("/v1/me")).status_code == 401


# 6. Repeated callbacks map to one account and one personal workspace.
async def test_repeated_sign_in_reuses_the_account(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    assert (await _sign_in(app, 9401, "first-login")).status_code == 200
    assert (await _sign_in(app, 9401, "renamed-login")).status_code == 200
    async with db() as s:
        users = (
            await s.execute(select(UserModel).where(UserModel.github_user_id == 9401))
        ).scalars()
        assert [u.github_login for u in users] == ["renamed-login"]  # identity is the numeric ID
    assert await _personal_workspaces(db, 9401) == 1


async def test_a_reused_login_name_is_a_different_account(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    await _sign_in(app, 9501, "same-name")
    await _sign_in(app, 9502, "same-name")
    async with db() as s:
        count = (await s.execute(select(func.count()).select_from(UserModel))).scalar_one()
    assert count == 2


# 7, 8, 11. Tenant isolation, non-members, revoked members.
async def _seed_incident(
    db: async_sessionmaker[AsyncSession], installation_id: int, incident_id: str
) -> None:
    now = datetime.now(UTC)
    async with db() as s:
        await PipelineRepository(s).create_pipeline(
            PipelineRecord(
                pipeline_id=f"pipe_{incident_id}",
                incident_id=incident_id,
                repository_owner="acme",
                repository_name="private-repo",
                run_id=1,
                commit_sha="a" * 40,
                installation_id=installation_id,
                status=PipelineStatus.RECEIVED,
                created_at=now,
                updated_at=now,
            )
        )


async def test_user_a_cannot_read_user_b_workspace(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    async with db() as s:
        user_a = await seed_user(s, 9601)
        user_b = await seed_user(s, 9602)
        await link_installation(s, user_b.workspace_id, 960_200, user_b.user_id)
    await _seed_incident(db, 960_200, f"inc_b_{suffix}")

    async with _client(app, user_a) as client:
        base = f"/v1/workspaces/{user_b.workspace_id}"
        for path in ("/incidents", f"/incidents/inc_b_{suffix}", "/pipelines", "/members"):
            response = await client.get(base + path)
            assert response.status_code == 404, path
            assert response.json() == {"detail": "workspace_not_found"}
        # Their own workspace, with the other tenant's incident ID: still not found.
        own = f"/v1/workspaces/{user_a.workspace_id}"
        assert (await client.get(f"{own}/incidents/inc_b_{suffix}")).status_code == 404
        assert (await client.get(f"{own}/incidents")).json() == []
        # Writes are refused the same way.
        renamed = await client.patch(base, json={"name": "Taken"}, headers=CSRF)
        assert renamed.status_code == 404

    async with _client(app, user_b) as client:
        listed = (await client.get(f"/v1/workspaces/{user_b.workspace_id}/incidents")).json()
    assert [i["incident_id"] for i in listed] == [f"inc_b_{suffix}"]


async def test_unlinked_or_unverified_incidents_belong_to_nobody(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        user = await seed_user(s, 9651)
    await _seed_incident(db, 965_100, "inc_orphan")  # no workspace links installation 965100
    async with _client(app, user) as client:
        own = f"/v1/workspaces/{user.workspace_id}"
        assert (await client.get(f"{own}/incidents")).json() == []
        assert (await client.get(f"{own}/incidents/inc_orphan")).status_code == 404


async def test_revoked_member_loses_access_on_the_next_request(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        owner = await seed_user(s, 9701)
        member = await seed_user(s, 9702)
        team = await seed_team(s, owner)
        await add_member(s, team, member.user_id, "viewer")
    async with _client(app, member) as client:
        assert (await client.get(f"/v1/workspaces/{team}/incidents")).status_code == 200
    async with _client(app, owner) as client:
        removed = await client.delete(
            f"/v1/workspaces/{team}/members/{member.user_id}", headers=CSRF
        )
        assert removed.status_code == 204
    async with _client(app, member) as client:  # same session, membership gone
        assert (await client.get(f"/v1/workspaces/{team}/incidents")).status_code == 404


# 10. No self-escalation, no granting above your own role.
async def test_members_cannot_escalate_roles(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        owner = await seed_user(s, 9801)
        admin = await seed_user(s, 9802)
        viewer = await seed_user(s, 9803)
        team = await seed_team(s, owner)
        await add_member(s, team, admin.user_id, "admin")
        await add_member(s, team, viewer.user_id, "viewer")
    members = f"/v1/workspaces/{team}/members"

    async with _client(app, viewer) as client:
        mine = await client.patch(
            f"{members}/{viewer.user_id}", json={"role": "owner"}, headers=CSRF
        )
        assert mine.status_code == 403
        other = await client.patch(
            f"{members}/{admin.user_id}", json={"role": "viewer"}, headers=CSRF
        )
        assert other.status_code == 403

    async with _client(app, admin) as client:
        self_up = await client.patch(
            f"{members}/{admin.user_id}", json={"role": "owner"}, headers=CSRF
        )
        assert self_up.status_code == 403
        to_admin = await client.patch(
            f"{members}/{viewer.user_id}", json={"role": "admin"}, headers=CSRF
        )
        assert to_admin.status_code == 403  # only strictly lower roles
        demote_owner = await client.patch(
            f"{members}/{owner.user_id}", json={"role": "viewer"}, headers=CSRF
        )
        assert demote_owner.status_code == 403
        self_slack = await client.put(
            f"{members}/{admin.user_id}/slack", json={"slack_user_id": "U0ADMIN"}, headers=CSRF
        )
        assert self_slack.status_code == 403  # an approval identity is never self-claimed
        ok = await client.patch(
            f"{members}/{viewer.user_id}", json={"role": "approver"}, headers=CSRF
        )
        assert ok.status_code == 204

    async with _client(app, owner) as client:
        last_owner = await client.delete(f"{members}/{owner.user_id}", headers=CSRF)
        assert last_owner.status_code == 400  # use `leave`; and the last owner cannot leave
        leave = await client.post(f"/v1/workspaces/{team}/leave", headers=CSRF)
        assert leave.status_code == 409

    async with db() as s:
        roles = dict(
            (
                await s.execute(
                    select(WorkspaceMembershipModel.user_id, WorkspaceMembershipModel.role).where(
                        WorkspaceMembershipModel.workspace_id == team
                    )
                )
            ).all()
        )
    assert roles == {owner.user_id: "owner", admin.user_id: "admin", viewer.user_id: "approver"}


# 9 and 12. Approvals and background work stay inside the owning workspace.
async def test_workspace_approver_check_is_scoped_to_the_owning_workspace(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    now = datetime.now(UTC)
    async with db() as s:
        owner = await seed_user(s, 9901)
        approver = await seed_user(s, 9902)
        viewer = await seed_user(s, 9903)
        outsider_owner = await seed_user(s, 9904)
        team = await seed_team(s, owner)
        other_team = await seed_team(s, outsider_owner)
        await add_member(s, team, approver.user_id, "approver", slack_user_id="U0APPROVER")
        await add_member(s, team, viewer.user_id, "viewer", slack_user_id="U0VIEWER")
        await add_member(s, other_team, outsider_owner.user_id, "owner", slack_user_id="U0OTHER")
        await link_installation(s, team, 990_100, owner.user_id)
        await link_installation(s, other_team, 990_200, outsider_owner.user_id)
        await PipelineRepository(s).create_pipeline(
            PipelineRecord(
                pipeline_id="pipe_scoped",
                incident_id="inc_scoped",
                repository_owner="acme",
                repository_name="repo",
                run_id=1,
                commit_sha="b" * 40,
                installation_id=990_100,
                approval_id="appr_scoped",
                status=PipelineStatus.AWAITING_APPROVAL,
                created_at=now,
                updated_at=now,
            )
        )
        assert await workspace_approver_authorized(s, "appr_scoped", "U0APPROVER") is True
        assert await workspace_approver_authorized(s, "appr_scoped", "U0VIEWER") is False
        assert await workspace_approver_authorized(s, "appr_scoped", "U0OTHER") is False
        assert await workspace_approver_authorized(s, "appr_scoped", "") is False
        assert await workspace_approver_authorized(s, "appr_unknown", "U0APPROVER") is False

    async with _client(app, owner) as client:
        unlinked = await client.delete(f"/v1/workspaces/{team}/installations/990100", headers=CSRF)
        assert unlinked.status_code == 204
    async with db() as s:  # unlinking the installation ends approval authority at once
        assert await workspace_approver_authorized(s, "appr_scoped", "U0APPROVER") is False


# --- Invitations are bound to the immutable GitHub account ID --------------------------------

GITHUB_API = "https://api.github.com"


def _mock_github_user(login: str, github_id: int, account_type: str = "User") -> respx.Route:
    return respx.get(f"{GITHUB_API}/users/{login}").respond(
        json={
            "id": github_id,
            "login": login,
            "type": account_type,
            "avatar_url": f"https://avatars.githubusercontent.com/u/{github_id}",
        }
    )


async def _invite(
    app: FastAPI, owner: Tenant, team: str, login: str, github_id: int, role: str = "approver"
) -> httpx.Response:
    async with _client(app, owner) as client:
        with respx.mock:
            route = _mock_github_user(login, github_id)
            response = await client.post(
                f"/v1/workspaces/{team}/invitations",
                json={"github_login": f"@{login}", "role": role},
                headers=CSRF,
            )
            if response.status_code == 201:
                # Resolved with the inviter's own (server-side) GitHub token.
                auth = route.calls.last.request.headers["authorization"]
                assert auth == f"Bearer {github_token_for(10_001)}"
    return response


async def _rename(db: async_sessionmaker[AsyncSession], github_id: int, login: str) -> None:
    async with db() as s:
        await s.execute(
            update(UserModel)
            .where(UserModel.github_user_id == github_id)
            .values(github_login=login)
        )
        await s.commit()


async def _team_with_owner(db: async_sessionmaker[AsyncSession]) -> tuple[Tenant, str]:
    async with db() as s:
        owner = await seed_user(s, 10_001, "team-owner")
        return owner, await seed_team(s, owner)


async def test_invitation_resolves_and_shows_the_github_account(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    created = await _invite(app, owner, team, "Octo-Cat", 20_001)
    assert created.status_code == 201
    body = created.json()
    assert (body["github_user_id"], body["github_login"]) == (20_001, "Octo-Cat")
    assert body["avatar_url"] == "https://avatars.githubusercontent.com/u/20001"
    async with _client(app, owner) as client:
        listed = (await client.get(f"/v1/workspaces/{team}/invitations")).json()
    assert [(i["github_user_id"], i["github_login"]) for i in listed] == [(20_001, "Octo-Cat")]
    async with db() as s:
        stored = (await s.execute(select(WorkspaceInvitationModel))).scalar_one()
    assert stored.github_user_id == 20_001


async def test_invitation_refuses_unknown_and_non_user_accounts(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with _client(app, owner) as client:
        with respx.mock:
            respx.get(f"{GITHUB_API}/users/ghost-user").respond(404, json={})
            _mock_github_user("acme-org", 30_001, account_type="Organization")
            missing = await client.post(
                f"/v1/workspaces/{team}/invitations",
                json={"github_login": "ghost-user", "role": "viewer"},
                headers=CSRF,
            )
            org = await client.post(
                f"/v1/workspaces/{team}/invitations",
                json={"github_login": "acme-org", "role": "viewer"},
                headers=CSRF,
            )
    assert missing.status_code == 404 and missing.json() == {"detail": "github_user_not_found"}
    assert org.status_code == 400 and org.json() == {"detail": "github_account_not_a_user"}


async def test_original_user_can_accept_after_renaming(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with db() as s:
        invitee = await seed_user(s, 20_002, "old-name")
    invitation_id = (await _invite(app, owner, team, "old-name", 20_002)).json()["invitation_id"]
    await _rename(db, 20_002, "new-name")  # renamed on GitHub; seen at their next sign-in
    async with _client(app, invitee) as client:
        mine = (await client.get("/v1/me/invitations")).json()
        assert [i["invitation_id"] for i in mine] == [invitation_id]
        joined = await client.post(f"/v1/me/invitations/{invitation_id}/accept", headers=CSRF)
    assert joined.status_code == 200 and joined.json()["role"] == "approver"


async def test_a_new_holder_of_the_former_username_cannot_accept(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with db() as s:
        await seed_user(s, 20_003, "contested")
    invitation_id = (await _invite(app, owner, team, "contested", 20_003)).json()["invitation_id"]
    await _rename(db, 20_003, "moved-away")
    async with db() as s:  # someone else registers the freed username and signs in
        squatter = await seed_user(s, 20_004, "contested")
    async with _client(app, squatter) as client:
        assert (await client.get("/v1/me/invitations")).json() == []
        stolen = await client.post(f"/v1/me/invitations/{invitation_id}/accept", headers=CSRF)
    assert stolen.status_code == 404
    assert await _is_member(db, team, squatter) is False


async def test_wrong_numeric_github_id_is_rejected(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    # Same login as the invitation, different GitHub account: never a match.
    invitation_id = (await _invite(app, owner, team, "same-login", 20_005)).json()["invitation_id"]
    async with db() as s:
        other = await seed_user(s, 20_006, "same-login")
    async with _client(app, other) as client:
        response = await client.post(f"/v1/me/invitations/{invitation_id}/accept", headers=CSRF)
    assert response.status_code == 404
    assert await _is_member(db, team, other) is False


async def test_expired_invitation_is_rejected(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with db() as s:
        invitee = await seed_user(s, 20_007, "late")
    created = (await _invite(app, owner, team, "late", 20_007)).json()
    assert datetime.fromisoformat(created["expires_at"]) - datetime.fromisoformat(
        created["created_at"]
    ) == timedelta(days=7)
    async with db() as s:
        await s.execute(
            update(WorkspaceInvitationModel).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await s.commit()
    async with _client(app, invitee) as client:
        assert (await client.get("/v1/me/invitations")).json() == []
        response = await client.post(
            f"/v1/me/invitations/{created['invitation_id']}/accept", headers=CSRF
        )
    assert response.status_code == 404
    # An expired invitation does not block a fresh one for the same account.
    assert (await _invite(app, owner, team, "late", 20_007)).status_code == 201


async def test_duplicate_acceptance_is_safe(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with db() as s:
        invitee = await seed_user(s, 20_008, "twice")
    invitation_id = (await _invite(app, owner, team, "twice", 20_008)).json()["invitation_id"]
    url = f"/v1/me/invitations/{invitation_id}/accept"
    async with _client(app, invitee) as first, _client(app, invitee) as second:
        results = await asyncio.gather(
            first.post(url, headers=CSRF), second.post(url, headers=CSRF)
        )
    assert sorted(r.status_code for r in results) == [200, 404]
    async with _client(app, invitee) as client:
        assert (await client.post(url, headers=CSRF)).status_code == 404
    async with db() as s:
        count = (
            await s.execute(
                select(func.count())
                .select_from(WorkspaceMembershipModel)
                .where(
                    WorkspaceMembershipModel.workspace_id == team,
                    WorkspaceMembershipModel.user_id == invitee.user_id,
                )
            )
        ).scalar_one()
    assert count == 1
    # Inviting an existing member is refused, so no second invitation can change their role.
    assert (await _invite(app, owner, team, "twice", 20_008)).status_code == 409


async def test_personal_workspaces_take_no_invitations(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, _ = await _team_with_owner(db)
    response = await _invite(app, owner, owner.workspace_id, "someone", 20_009)
    assert response.status_code == 400 and response.json() == {"detail": "personal_workspace"}


async def _is_member(db: async_sessionmaker[AsyncSession], team: str, tenant: Tenant) -> bool:
    async with db() as s:
        role = (
            await s.execute(
                select(WorkspaceMembershipModel.role).where(
                    WorkspaceMembershipModel.workspace_id == team,
                    WorkspaceMembershipModel.user_id == tenant.user_id,
                )
            )
        ).scalar_one_or_none()
    return role is not None


# --- Installation linking: visibility is not authority ---------------------------------------

USER_ACCOUNT, ORG_ACCOUNT = 10_001, 40_001


def _mock_installation_flow(
    installation_id: int,
    *,
    account_id: int,
    account_login: str,
    account_type: str,
    membership: dict[str, object] | None = None,
    membership_status: int = 200,
    repo_owner_id: int | None = None,
    repositories: int = 2,
) -> dict[str, respx.Route]:
    """GitHub as the signed-in user (user token) and the App (JWT, installation token) see it."""
    routes = {
        "user_installations": respx.get(f"{GITHUB_API}/user/installations").respond(
            json={
                "installations": [
                    {
                        "id": installation_id,
                        "app_id": APP_ID,
                        "account": {"id": account_id, "login": account_login, "type": account_type},
                        "suspended_at": None,
                    }
                ]
            }
        ),
        "membership": respx.get(f"{GITHUB_API}/user/memberships/orgs/{account_login}").respond(
            membership_status, json=membership or {}
        ),
        "app_installation": respx.get(f"{GITHUB_API}/app/installations/{installation_id}").respond(
            json={"id": installation_id, "app_id": APP_ID, "suspended_at": None}
        ),
        "token": respx.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
        ).respond(
            201,
            json={
                "token": "ghs_synthetic",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {"metadata": "read"},
            },
        ),
        "repositories": respx.get(f"{GITHUB_API}/installation/repositories").respond(
            json={
                "repositories": [
                    {
                        "id": installation_id * 10 + n,
                        "full_name": f"{account_login}/repo{n}",
                        "private": True,
                        "owner": {"id": repo_owner_id if repo_owner_id is not None else account_id},
                    }
                    for n in range(repositories)
                ]
            }
        ),
    }
    return routes


def _org_membership(role: str, *, org_id: int = ORG_ACCOUNT, user_id: int = USER_ACCOUNT) -> dict:
    return {
        "state": "active",
        "role": role,
        "organization": {"id": org_id, "login": "acme"},
        "user": {"id": user_id},
    }


async def _link(
    app: FastAPI, tenant: Tenant, workspace_id: str, installation_id: int
) -> httpx.Response:
    async with _client(app, tenant) as client:
        return await client.post(
            f"/v1/workspaces/{workspace_id}/installations",
            json={"installation_id": installation_id},
            headers=CSRF,
        )


async def _linked(db: async_sessionmaker[AsyncSession]) -> list[tuple[int, str]]:
    async with db() as s:
        return [
            (row.installation_id, row.workspace_id)
            for row in (await s.execute(select(GitHubInstallationModel))).scalars()
        ]


async def test_user_links_the_installation_on_their_own_account(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, _ = await _team_with_owner(db)
    with respx.mock:
        routes = _mock_installation_flow(
            900_001, account_id=USER_ACCOUNT, account_login="team-owner", account_type="User"
        )
        response = await _link(app, owner, owner.workspace_id, 900_001)
        assert not routes["membership"].called  # user accounts need no organization check
        assert routes["token"].called  # the App confirmed the installation
    assert response.status_code == 201
    assert await _linked(db) == [(900_001, owner.workspace_id)]
    async with _client(app, owner) as client:
        repos = (await client.get(f"/v1/workspaces/{owner.workspace_id}/repositories")).json()
    assert [r["full_name"] for r in repos] == ["team-owner/repo0", "team-owner/repo1"]


async def test_another_users_account_installation_cannot_be_linked(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    """Visible (for example as a collaborator) is not owned."""
    owner, team = await _team_with_owner(db)
    with respx.mock:
        routes = _mock_installation_flow(
            900_002, account_id=55_555, account_login="someone-else", account_type="User"
        )
        personal = await _link(app, owner, owner.workspace_id, 900_002)
        shared = await _link(app, owner, team, 900_002)
        assert not routes["token"].called  # refused before any installation token is issued
    for response in (personal, shared):
        assert response.status_code == 403
        assert response.json() == {"detail": "installation_account_not_yours"}
    assert await _linked(db) == []


async def test_org_owner_links_an_org_installation_to_a_team_workspace(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    with respx.mock:
        routes = _mock_installation_flow(
            900_003,
            account_id=ORG_ACCOUNT,
            account_login="acme",
            account_type="Organization",
            membership=_org_membership("admin"),
        )
        response = await _link(app, owner, team, 900_003)
        auth = routes["membership"].calls.last.request.headers["authorization"]
    assert response.status_code == 201
    assert auth == f"Bearer {github_token_for(USER_ACCOUNT)}"  # the user's own token
    assert await _linked(db) == [(900_003, team)]


@pytest.mark.parametrize(
    ("membership", "status"),
    [
        (_org_membership("member"), 200),  # a normal member who can see the installation
        ({**_org_membership("admin"), "state": "pending"}, 200),  # invited, not yet a member
        (_org_membership("admin", org_id=40_999), 200),  # a different organization's ID
        (_org_membership("admin", user_id=77_777), 200),  # membership of someone else
        (None, 403),  # the App lacks "Members: read": unverifiable, so refused
        (None, 404),  # not a member at all
    ],
)
async def test_org_installation_requires_a_verified_org_owner(
    app: FastAPI,
    db: async_sessionmaker[AsyncSession],
    membership: dict[str, object] | None,
    status: int,
) -> None:
    owner, team = await _team_with_owner(db)
    with respx.mock:
        routes = _mock_installation_flow(
            900_004,
            account_id=ORG_ACCOUNT,
            account_login="acme",
            account_type="Organization",
            membership=membership,
            membership_status=status,
        )
        response = await _link(app, owner, team, 900_004)
        assert not routes["token"].called
    assert response.status_code == 403 and response.json() == {"detail": "org_owner_required"}
    assert await _linked(db) == []


async def test_org_installation_never_goes_into_a_personal_workspace(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    """Even an organization owner cannot attach an organization-wide installation to a
    personal workspace; a plain member certainly cannot."""
    owner, _ = await _team_with_owner(db)
    for role in ("member", "admin"):
        with respx.mock:
            _mock_installation_flow(
                900_005,
                account_id=ORG_ACCOUNT,
                account_login="acme",
                account_type="Organization",
                membership=_org_membership(role),
            )
            response = await _link(app, owner, owner.workspace_id, 900_005)
        assert response.status_code == 403
        assert response.json() == {"detail": "personal_workspace_own_account_only"}
    assert await _linked(db) == []


async def test_installation_not_visible_to_the_user_cannot_be_linked(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    with respx.mock:
        _mock_installation_flow(
            900_006, account_id=USER_ACCOUNT, account_login="team-owner", account_type="User"
        )
        response = await _link(app, owner, team, 900_999)  # an ID chosen by the client
    assert response.status_code == 403 and response.json() == {
        "detail": "installation_not_accessible"
    }


@pytest.mark.parametrize(("owner_id", "repositories"), [(66_666, 2), (None, 0)])
async def test_installation_ownership_is_verified_through_the_app(
    app: FastAPI,
    db: async_sessionmaker[AsyncSession],
    owner_id: int | None,
    repositories: int,
) -> None:
    """The App-issued token's repositories must exist and belong to the reported account."""
    owner, _ = await _team_with_owner(db)
    with respx.mock:
        _mock_installation_flow(
            900_007,
            account_id=USER_ACCOUNT,
            account_login="team-owner",
            account_type="User",
            repo_owner_id=owner_id,
            repositories=repositories,
        )
        response = await _link(app, owner, owner.workspace_id, 900_007)
    assert response.status_code == 409
    assert response.json()["detail"] in (
        "installation_ownership_mismatch",
        "installation_has_no_repositories",
    )
    assert await _linked(db) == []


async def test_installation_not_owned_by_this_app_is_refused(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, _ = await _team_with_owner(db)
    with respx.mock:
        routes = _mock_installation_flow(
            900_008, account_id=USER_ACCOUNT, account_login="team-owner", account_type="User"
        )
        routes["app_installation"].respond(json={"id": 900_008, "app_id": 1, "suspended_at": None})
        response = await _link(app, owner, owner.workspace_id, 900_008)
    assert response.status_code == 502 and response.json() == {
        "detail": "installation_not_verified"
    }
    assert await _linked(db) == []


async def test_an_installation_belongs_to_one_workspace(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    flow = {
        "account_id": ORG_ACCOUNT,
        "account_login": "acme",
        "account_type": "Organization",
        "membership": _org_membership("admin"),
    }
    async with db() as s:
        second_team = await seed_team(s, owner, "Other team")
    with respx.mock:
        _mock_installation_flow(900_009, **flow)  # type: ignore[arg-type]
        assert (await _link(app, owner, team, 900_009)).status_code == 201
        again = await _link(app, owner, second_team, 900_009)
    assert again.status_code == 409 and again.json() == {"detail": "installation_already_linked"}
    assert await _linked(db) == [(900_009, team)]


async def test_only_workspace_admins_link_installations(
    app: FastAPI, db: async_sessionmaker[AsyncSession]
) -> None:
    owner, team = await _team_with_owner(db)
    async with db() as s:
        maintainer = await seed_user(s, USER_ACCOUNT + 1, "maint")
        await add_member(s, team, maintainer.user_id, "maintainer")
    with respx.mock:
        routes = _mock_installation_flow(
            900_010, account_id=USER_ACCOUNT + 1, account_login="maint", account_type="User"
        )
        response = await _link(app, maintainer, team, 900_010)
        assert not routes["user_installations"].called
    assert response.status_code == 403 and response.json() == {"detail": "insufficient_role"}
