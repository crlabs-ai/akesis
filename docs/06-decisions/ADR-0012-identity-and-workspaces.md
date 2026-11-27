# ADR-0012: GitHub Sign-In, Workspaces and Tenant Isolation

## Status
Accepted

## Date
2026-11-27

## Context
Until Phase 2, Akesis had no user identity. The read API was guarded by one global
`X-Akesis-Read-Token` that an operator typed into the console, and every holder of that token saw
every incident, pipeline and delivery. The only approval control was the global Slack allowlist
(ADR-0006, ADR-0010 condition 5). That does not support more than one team, and a shared bearer
token in browser storage is a weak credential.

Akesis already runs as a GitHub App, and every admitted pipeline records the App installation it
came from in verified provenance (ADR-0010, Phase 1.8). That installation is a natural tenant
boundary: GitHub already decides who can access it.

## Decision

### 1. Sign-in: the existing GitHub App's user authorization, through Authlib
* OAuth 2.0 authorization code with PKCE (S256), using the App's client ID and secret. Authlib
  (`authlib>=1.6,<2`) builds the authorization URL and performs the code exchange. No other
  identity provider is added.
* `state` (32 random bytes) and the PKCE verifier are stored server-side, hashed and single use,
  and expire after ten minutes. Each sign-in attempt is bound to the browser that started it by a
  separate HttpOnly cookie (`akesis_oauth`, path `/v1/auth`). The attempt is consumed and committed
  before the code exchange, so a replayed or raced callback is refused.
* Users are identified by GitHub's immutable numeric user ID, never by login. A repeated or
  concurrent callback updates the same user.
* On first sign-in a user receives exactly one personal workspace. A unique key on
  `workspaces.personal_owner_id` plus `ON CONFLICT DO NOTHING` makes this race-safe.
* `return_to` accepts only same-site `/app` paths; anything else becomes `/app`.

### 2. Sessions: server-side, opaque, HttpOnly
* The browser holds one random 256-bit token in the `akesis_session` cookie: HttpOnly,
  `SameSite=Lax`, `Secure` (required in production). PostgreSQL stores only its SHA-256.
* Sessions expire (`AUTH_SESSION_TTL_HOURS`, default 12) and are checked against the database on
  every request, so logout, "sign out everywhere" and expiry apply immediately.
* The GitHub user-to-server token is encrypted at rest with Fernet (`AUTH_TOKEN_ENCRYPTION_KEY`)
  and used only server-side to list the App installations the user can access. It is deleted when
  the session is revoked. No GitHub credential, App key or access token reaches browser code.
* CSRF: every state-changing route requires the `X-Akesis-CSRF: 1` header, which a cross-site page
  cannot send without a CORS preflight, and rejects a present `Origin` outside
  `CORS_ALLOWED_ORIGINS`.

### 3. Workspaces and roles
* Entities: `users`, `auth_sessions`, `oauth_login_attempts`, `workspaces` (personal or team),
  `workspace_memberships`, `workspace_invitations`, `github_installations`,
  `workspace_repositories` (migration `0010_identity_workspaces`).
* Roles, highest first: OWNER, ADMIN, MAINTAINER, APPROVER, VIEWER.
* Nobody changes their own role. An actor assigns, changes or removes only roles strictly below
  their own; owners can manage owners. A workspace always keeps at least one owner (owner rows are
  locked while counting).
* Team workspaces invite by GitHub username, which is resolved once, at creation, to GitHub's
  immutable numeric account ID (`GET /users/{username}` with the inviter's token; organizations
  and bots are refused). The invitation stores that ID; the username is display-only. Only the
  signed-in user with that numeric ID can see or accept it, so a rename never moves an invitation
  and a new holder of a freed username cannot claim it. Invitations expire after 7 days, accept
  once (row lock), and never change an existing member's role.

### 4. Tenancy follows the GitHub App installation
* A pipeline belongs to the workspace that links the installation recorded in its **verified**
  provenance (`pipelines.installation_id`). Its approval, mutation, logs and delivery records
  follow it. Existing rows are backfilled from verified provenance only; rows without one belong
  to no workspace and are visible to nobody.
* **Visibility is not authority.** `GET /user/installations` lists every installation a user can
  merely read, so it is only the first gate. Linking requires, in order, before any installation
  token is issued:
  1. the caller is an OWNER or ADMIN of the target workspace;
  2. the installation is in the caller's own `GET /user/installations` for this App and not
     suspended;
  3. **user-account installation:** the account's numeric ID equals the caller's GitHub ID;
     **organization installation:** the target is a team workspace, and
     `GET /user/memberships/orgs/{org}` (the caller's own token) reports `state: active`,
     `role: admin` (organization owner), the organization's numeric ID equal to the installation
     account, and the caller's own user ID. Anything GitHub will not confirm (403 when the App
     lacks the organization "Members: read" permission, 404 for non-members) is refused. Other
     account types are refused;
  4. an App-issued, metadata-only installation token (App JWT: same App, same ID, not suspended)
     lists at least one repository, and every repository is owned by that account.
  The installation and its repositories are then stored in one transaction.
* An installation belongs to at most one workspace (primary key). Moving it requires the current
  workspace to unlink it first; there is no shared or ambiguous ownership.

### 5. Enforcement is in the backend data and service layers
* `tenancy.authorize()` is the only issuer of a `WorkspaceScope`, and it checks membership in
  PostgreSQL on every request. Repository reads (`list_incidents`, `get_incident`,
  `list_recent_pipelines`, `list_deliveries`) require a scope and filter in SQL by the workspace's
  installations. Unscoped variants no longer exist.
* A workspace ID in a URL is an identifier, never a credential. Non-members get 404 (existence is
  not revealed); members without the role get 403.
* The global read token is removed completely, with no backward-compatible global read routes.

### 6. Approvals: Slack only, plus the workspace role
* Slack stays the only approval channel. The global allowlist, workspace and channel checks are
  unchanged and still required. In addition, the clicking Slack user must be mapped to an
  APPROVER-or-higher member of the workspace that owns the incident, by **owner attestation**:
  * only a workspace OWNER creates, changes or revokes a mapping (admins cannot);
  * an owner's own Slack ID (mapped to them in any workspace) is never put on another member;
  * only members who may approve (APPROVER, MAINTAINER, ADMIN, OWNER) can be mapped;
  * each mapping records `slack_mapped_by` and `slack_mapped_at` (migration
    `0011_slack_mapping_attestation`);
  * a mapping authorizes an approval only while it has both, its attester is still an OWNER of
    that workspace, and the member's current role may approve. Revoked, unattributed (legacy) or
    orphaned mappings fail closed.
* The same check runs again when the remediation job executes, and the job refuses a pipeline
  whose `installation_id` differs from its verified provenance. Removing a member, lowering their
  role or unlinking the installation stops their pending approvals.

### Unchanged
`require_verified_provenance()` and the remediation hold (ADR-0010), executor socket
authorization, Docker isolation, GitHub mutation safeguards and the no-auto-merge rule. The new
checks run after the hold, never instead of it.

### Not yet: verified Slack identity
Owner attestation means an accountable owner vouches that a Slack user ID belongs to a member. It
is **not** cryptographic proof that the member controls that Slack account, and an owner who
attests a false mapping can misattribute approvals (bounded by the global allowlist). Binding a
Slack identity to the member themselves, for example with Sign in with Slack (OpenID Connect) or
a code delivered in Slack, is a separate future security task that needs its own ADR.

## Consequences
* The console needs a GitHub App with user authorization enabled (client ID, client secret,
  callback URL) and a Fernet key. Setup: `docs/05-development/local-development.md`.
* Linking an organization installation needs the App's organization permission "Members:
  read-only"; without it, organization links fail closed with `org_owner_required`.
* Owner status is verified at link time. Losing organization ownership later does not unlink the
  installation; a workspace owner or admin unlinks it.
* Authlib 1.9 emits a deprecation warning recommending `httpx2`; behaviour is unaffected.

## Alternatives considered
* **Keep the read token beside sessions:** rejected. Two credentials double the attack surface,
  and a global token cannot be tenant-scoped.
* **A separate OAuth App or an identity provider:** rejected. The GitHub App already exists and
  its user authorization is GitHub's recommended flow.
* **Stateless signed-cookie sessions (JWT):** rejected. They cannot be revoked immediately.
