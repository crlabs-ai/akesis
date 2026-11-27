import React, { useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient, refreshSession } from '../../services/api'
import { session } from '../../services/session'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { InstallationsSection } from './InstallationsSection'
import { actionMessage, useWorkspaceData } from './useAsync'
import styles from './Workspace.module.css'

const loadRepositories = () => apiClient.getRepositories()

/** Profile, sessions, the selected workspace, and its GitHub installations and repositories. */
export const SettingsView: React.FC = () => {
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const navigate = useNavigate()
  const repositories = useWorkspaceData(loadRepositories)
  const [message, setMessage] = useState<string | null>(null)
  const [teamName, setTeamName] = useState('')
  const [rename, setRename] = useState('')
  const { me, workspace } = state
  if (!me || !workspace) return null
  const owner = workspace.role === 'owner'

  const act = async (action: () => Promise<unknown>) => {
    setMessage(null)
    try {
      await action()
    } catch (reason) {
      setMessage(actionMessage(reason))
    }
  }

  const signOut = (everywhere: boolean) =>
    act(async () => {
      await (everywhere ? apiClient.signOutEverywhere() : apiClient.signOut())
      session.signedOut(null)
      navigate('/login', { replace: true })
    })

  return (
    <div className={styles.view}>
      <header className={styles.head}>
        <h1 className={styles.title}>Settings</h1>
        <p className={styles.sub}>Your profile, sessions and the {workspace.name} workspace.</p>
      </header>

      {message && (
        <p className={styles.message} role="alert">
          {message}
        </p>
      )}

      <section className={styles.section} aria-labelledby="profile-title">
        <h2 id="profile-title" className={styles.sectionTitle}>
          Profile
        </h2>
        <div className={styles.person}>
          {me.avatar_url && <img className={styles.avatar} src={me.avatar_url} alt="" />}
          <span>
            {me.display_name ?? me.github_login} <span className="mono muted">@{me.github_login}</span>
          </span>
        </div>
        <p className={styles.note}>
          Signed in with GitHub. This session expires {new Date(me.session_expires_at).toLocaleString()}.
        </p>
        <div className={styles.row}>
          <button type="button" className="btn" onClick={() => void signOut(false)}>
            Sign out
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => void signOut(true)}>
            Sign out of every session
          </button>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="workspace-title">
        <h2 id="workspace-title" className={styles.sectionTitle}>
          Workspace
        </h2>
        <p className={styles.note}>
          {workspace.kind === 'personal' ? 'Personal workspace' : 'Team workspace'} · <span className="mono">{workspace.slug}</span> · your role: {workspace.role}
        </p>
        {workspace.kind === 'team' && owner && (
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault()
              void act(async () => {
                await apiClient.renameWorkspace(rename)
                setRename('')
                await refreshSession()
              })
            }}
          >
            <label className={styles.label}>
              New name
              <input className="input-text" value={rename} onChange={(e) => setRename(e.target.value)} required minLength={2} maxLength={100} />
            </label>
            <button type="submit" className="btn">
              Rename
            </button>
          </form>
        )}
        {workspace.kind === 'team' && (
          <div className={styles.row}>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() =>
                void act(async () => {
                  await apiClient.leaveWorkspace()
                  await refreshSession()
                })
              }
            >
              Leave workspace
            </button>
          </div>
        )}
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault()
            void act(async () => {
              const created = await apiClient.createWorkspace(teamName)
              setTeamName('')
              await refreshSession()
              session.selectWorkspace(created.workspace_id)
            })
          }}
        >
          <label className={styles.label}>
            Create a team workspace
            <input className="input-text" value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="Platform team" required minLength={2} maxLength={100} />
          </label>
          <button type="submit" className="btn">
            Create
          </button>
        </form>
      </section>

      <InstallationsSection onChange={repositories.reload} />

      <section className={styles.section} aria-labelledby="repos-title">
        <h2 id="repos-title" className={styles.sectionTitle}>
          Repositories
        </h2>
        {repositories.error ? (
          <ApiErrorPanel error={repositories.error} onRetry={repositories.reload} subject="repositories" />
        ) : (repositories.data ?? []).length === 0 ? (
          <p className={styles.empty}>
            {repositories.loading ? 'Loading…' : 'No repositories yet. Link a GitHub App installation above.'}
          </p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col">Visibility</th>
              </tr>
            </thead>
            <tbody>
              {repositories.data?.map((repo) => (
                <tr key={repo.github_repository_id}>
                  <td className="mono">{repo.full_name}</td>
                  <td>{repo.private ? 'private' : 'public'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
