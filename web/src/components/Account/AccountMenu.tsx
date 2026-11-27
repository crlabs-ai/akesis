import React, { useSyncExternalStore } from 'react'
import { session } from '../../services/session'

/** Header control: who is signed in and which workspace every view is scoped to. */
export const AccountMenu: React.FC = () => {
  const { me, workspace } = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  if (!me || !workspace) return null
  return (
    <div className="access-control">
      <label className="sr-only" htmlFor="workspace-select">
        Workspace
      </label>
      <select
        id="workspace-select"
        className="input-text"
        value={workspace.workspace_id}
        onChange={(event) => session.selectWorkspace(event.target.value)}
      >
        {me.workspaces.map((w) => (
          <option key={w.workspace_id} value={w.workspace_id}>
            {w.name} · {w.role}
          </option>
        ))}
      </select>
      {me.avatar_url ? (
        <img className="account-avatar" src={me.avatar_url} alt={`Signed in as ${me.github_login}`} width={22} height={22} />
      ) : (
        <span className="mono muted">@{me.github_login}</span>
      )}
    </div>
  )
}
