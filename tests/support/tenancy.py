"""Seed users, sessions, workspaces and installation links in the disposable test database.

Users are provisioned through the real `auth_service.provision_user`, so every seeded user gets
exactly the personal workspace a real sign-in would create.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.packages.database.models import (
    AuthSessionModel,
    GitHubInstallationModel,
    WorkspaceMembershipModel,
    WorkspaceModel,
)
from src.packages.shared.auth_service import SESSION_COOKIE, digest, new_id, provision_user
from src.packages.shared.models import RepositoryIdentity, WorkflowRunProvenance

CSRF = {"X-Akesis-CSRF": "1"}
# Synthetic Fernet key: test apps set AUTH_TOKEN_ENCRYPTION_KEY to this so seeded sessions carry a
# decryptable (fake) GitHub user token, `ghu_test_<github user id>`.
TEST_FERNET_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="


def github_token_for(github_user_id: int) -> str:
    return f"ghu_test_{github_user_id}"


@dataclass(frozen=True)
class Tenant:
    user_id: str
    workspace_id: str
    session_token: str

    @property
    def cookies(self) -> dict[str, str]:
        return {SESSION_COOKIE: self.session_token}


async def reset_identity(db: AsyncSession) -> None:
    await db.execute(
        text("TRUNCATE users, workspaces, github_installations, oauth_login_attempts CASCADE")
    )
    await db.commit()


async def open_session(
    db: AsyncSession,
    user_id: str,
    *,
    expires_in: timedelta = timedelta(hours=1),
    github_token: str | None = None,
) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        AuthSessionModel(
            session_id=new_id("ses"),
            token_sha256=digest(token),
            user_id=user_id,
            github_token_ciphertext=(
                Fernet(TEST_FERNET_KEY.encode()).encrypt(github_token.encode()).decode()
                if github_token
                else None
            ),
            github_token_expires_at=now + expires_in if github_token else None,
            created_at=now,
            expires_at=now + expires_in,
            last_seen_at=now,
        )
    )
    await db.commit()
    return token


async def seed_user(db: AsyncSession, github_user_id: int, login: str | None = None) -> Tenant:
    """A signed-in user and their personal workspace."""
    now = datetime.now(UTC)
    profile = {"id": github_user_id, "login": login or f"user{github_user_id}", "name": None}
    user_id = await provision_user(db, {**profile, "avatar": None}, now=now)
    workspace_id = (
        await db.execute(
            text("SELECT workspace_id FROM workspaces WHERE personal_owner_id = :u"),
            {"u": user_id},
        )
    ).scalar_one()
    session_token = await open_session(db, user_id, github_token=github_token_for(github_user_id))
    return Tenant(user_id, workspace_id, session_token)


async def seed_team(db: AsyncSession, owner: Tenant, name: str = "Team") -> str:
    now = datetime.now(UTC)
    workspace_id = new_id("ws")
    db.add(
        WorkspaceModel(
            workspace_id=workspace_id,
            kind="team",
            name=name,
            slug=f"team-{secrets.token_hex(4)}",
            created_by=owner.user_id,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        WorkspaceMembershipModel(
            workspace_id=workspace_id,
            user_id=owner.user_id,
            role="owner",
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return workspace_id


async def add_member(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    role: str,
    *,
    slack_user_id: str | None = None,
    attested_by: str | None = None,
) -> None:
    """Insert or update a membership. A Slack mapping authorizes approvals only with an
    attesting owner (`attested_by`); without it the mapping is unattributed (legacy)."""
    now = datetime.now(UTC)
    attestation = {
        "slack_mapped_by": attested_by if slack_user_id else None,
        "slack_mapped_at": now if slack_user_id and attested_by else None,
    }
    await db.execute(
        insert(WorkspaceMembershipModel)
        .values(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            slack_user_id=slack_user_id,
            **attestation,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "user_id"],
            set_={"role": role, "slack_user_id": slack_user_id, **attestation},
        )
    )
    await db.commit()


async def link_installation(
    db: AsyncSession, workspace_id: str, installation_id: int, linked_by: str
) -> None:
    db.add(
        GitHubInstallationModel(
            installation_id=installation_id,
            workspace_id=workspace_id,
            account_id=installation_id,
            account_login=f"acct{installation_id}",
            account_type="Organization",
            linked_by=linked_by,
            linked_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def seed_approver_workspace(
    db: AsyncSession, installation_id: int, *slack_user_ids: str, role: str = "approver"
) -> Tenant:
    """A team workspace that owns `installation_id`, with each Slack user mapped to a member
    holding `role`. Returns the first mapped member."""
    owner = await seed_user(db, 1 + secrets.randbelow(2**40))
    workspace_id = await seed_team(db, owner)
    await link_installation(db, workspace_id, installation_id, owner.user_id)
    members = []
    for slack_user_id in slack_user_ids:
        member = await seed_user(db, 1 + secrets.randbelow(2**40))
        await add_member(
            db,
            workspace_id,
            member.user_id,
            role,
            slack_user_id=slack_user_id,
            attested_by=owner.user_id,
        )
        members.append(Tenant(member.user_id, workspace_id, member.session_token))
    return members[0] if members else Tenant(owner.user_id, workspace_id, owner.session_token)


def verified_provenance(
    installation_id: int,
    *,
    run_id: int,
    head_sha: str,
    full_name: str = "crlabs-ai/akesis",
) -> WorkflowRunProvenance:
    """Verified provenance anchoring a test incident to `installation_id`."""
    repo = RepositoryIdentity(id=1001, full_name=full_name)
    return WorkflowRunProvenance(
        delivery_id=f"delivery-{secrets.token_hex(6)}",
        event_repository=repo,
        source_repository=repo,
        head_sha=head_sha,
        head_branch="main",
        run_id=run_id,
        run_attempt=1,
        installation_id=installation_id,
        verified_at=datetime.now(UTC),
    )
