import React, { useState, useSyncExternalStore } from 'react'
import { apiClient } from '../../services/api'
import { session } from '../../services/session'
import { WORKSPACE_ROLES, type WorkspaceMember, type WorkspaceRole } from '../../types/api'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { actionMessage, useWorkspaceData } from './useAsync'
import styles from './Workspace.module.css'

const RANK: Record<WorkspaceRole, number> = { owner: 5, admin: 4, maintainer: 3, approver: 2, viewer: 1 }

/** Mirrors the backend rule for display only; the backend enforces it. */
function assignable(actor: WorkspaceRole): WorkspaceRole[] {
  return WORKSPACE_ROLES.filter((role) => actor === 'owner' || RANK[role] < RANK[actor])
}

const loadMembers = async () => {
  const [members, invitations] = await Promise.all([
    apiClient.getMembers(),
    apiClient.getInvitations().catch(() => []), // admins and owners only
  ])
  return { members, invitations }
}

export const MembersView: React.FC = () => {
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const workspace = state.workspace
  const me = state.me
  const { data, error, loading, reload } = useWorkspaceData(loadMembers)
  const [message, setMessage] = useState<string | null>(null)
  const [invited, setInvited] = useState<string | null>(null)
  const [login, setLogin] = useState('')
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>('viewer')

  if (!workspace || !me) return null
  const role = workspace.role
  const manager = RANK[role] >= RANK.admin
  const isTeam = workspace.kind === 'team'

  const act = async (action: () => Promise<unknown>) => {
    setMessage(null)
    try {
      await action()
      reload()
    } catch (reason) {
      setMessage(actionMessage(reason))
    }
  }

  const canManage = (member: WorkspaceMember) =>
    member.user_id !== me.user_id && (role === 'owner' || RANK[member.role] < RANK[role])

  return (
    <div className={styles.view}>
      <header className={styles.head}>
        <h1 className={styles.title}>Members</h1>
        <p className={styles.sub}>
          {workspace.name} · your role: <strong>{role}</strong>. Approvers and above can approve
          remediation in Slack once an owner or admin maps their Slack user ID.
        </p>
      </header>

      {message && (
        <p className={styles.message} role="alert">
          {message}
        </p>
      )}

      <section className={styles.section} aria-labelledby="members-title">
        <div className={styles.sectionHead}>
          <h2 id="members-title" className={styles.sectionTitle}>
            People
          </h2>
          {loading && <span className={styles.note}>Loading…</span>}
        </div>
        {error ? (
          <ApiErrorPanel error={error} onRetry={reload} subject="members" />
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Member</th>
                <th scope="col">Role</th>
                {manager && <th scope="col">Slack user ID</th>}
                {manager && <th scope="col"><span className="sr-only">Actions</span></th>}
              </tr>
            </thead>
            <tbody>
              {(data?.members ?? []).map((member) => (
                <tr key={member.user_id}>
                  <td>
                    <span className={styles.person}>
                      {member.avatar_url ? (
                        <img className={styles.avatar} src={member.avatar_url} alt="" />
                      ) : (
                        <span className={styles.avatar} aria-hidden="true" />
                      )}
                      <span className="mono">{member.github_login}</span>
                      {member.user_id === me.user_id && <span className="badge badge-neutral">you</span>}
                    </span>
                  </td>
                  <td>
                    {canManage(member) ? (
                      <select
                        className="input-text"
                        aria-label={`Role for ${member.github_login}`}
                        value={member.role}
                        onChange={(event) =>
                          void act(() => apiClient.changeRole(member.user_id, event.target.value as WorkspaceRole))
                        }
                      >
                        {[...new Set([member.role, ...assignable(role)])].map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    ) : (
                      member.role
                    )}
                  </td>
                  {manager && (
                    <td>
                      <SlackField member={member} editable={canManage(member) || role === 'owner'} onSave={act} />
                    </td>
                  )}
                  {manager && (
                    <td>
                      {canManage(member) && (
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => void act(() => apiClient.removeMember(member.user_id))}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {isTeam && manager && (
        <section className={styles.section} aria-labelledby="invite-title">
          <h2 id="invite-title" className={styles.sectionTitle}>
            Invite by GitHub username
          </h2>
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault()
              setInvited(null)
              void act(async () => {
                const created = await apiClient.invite(login, inviteRole)
                setInvited(
                  `Invited @${created.github_login} (GitHub account ${created.github_user_id}) as ${created.role}. Only that account can accept, even if its username changes.`
                )
                setLogin('')
              })
            }}
          >
            <label className={styles.label}>
              GitHub username
              <input
                className="input-text"
                value={login}
                onChange={(event) => setLogin(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
                maxLength={40}
              />
            </label>
            <label className={styles.label}>
              Role
              <select
                className="input-text"
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value as WorkspaceRole)}
              >
                {assignable(role).map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="btn">
              Send invitation
            </button>
          </form>
          {invited && (
            <p className={styles.note} role="status">
              {invited}
            </p>
          )}
          <p className={styles.note}>
            The username is resolved to its GitHub account when you invite; that account signs in
            and accepts from its console. Invitations expire after 7 days.
          </p>
          {(data?.invitations ?? []).length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Pending</th>
                  <th scope="col">Role</th>
                  <th scope="col" className={styles.hideNarrow}>Expires</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data?.invitations.map((invitation) => (
                  <tr key={invitation.invitation_id}>
                    <td>
                      <span className="mono">@{invitation.github_login}</span>{' '}
                      <span className="muted">· GitHub ID {invitation.github_user_id}</span>
                    </td>
                    <td>{invitation.role}</td>
                    <td className={styles.hideNarrow}>{new Date(invitation.expires_at).toLocaleDateString()}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => void act(() => apiClient.revokeInvitation(invitation.invitation_id))}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {!isTeam && (
        <p className={styles.note}>
          This is your personal workspace. Create a team workspace in Settings to invite others.
        </p>
      )}
    </div>
  )
}

const SlackField: React.FC<{
  member: WorkspaceMember
  editable: boolean
  onSave: (action: () => Promise<unknown>) => Promise<void>
}> = ({ member, editable, onSave }) => {
  const [value, setValue] = useState(member.slack_user_id ?? '')
  if (!editable) return <span className="mono">{member.slack_user_id ?? '—'}</span>
  return (
    <form
      className={styles.row}
      onSubmit={(event) => {
        event.preventDefault()
        void onSave(() => apiClient.setSlackUser(member.user_id, value.trim() || null))
      }}
    >
      <input
        className="input-text mono"
        aria-label={`Slack user ID for ${member.github_login}`}
        placeholder="U0123ABCD"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        maxLength={32}
        spellCheck={false}
      />
      <button type="submit" className="btn btn-sm">
        Save
      </button>
    </form>
  )
}
