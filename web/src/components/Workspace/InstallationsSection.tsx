import React, { useState, useSyncExternalStore } from 'react'
import { apiClient } from '../../services/api'
import { session } from '../../services/session'
import type { GitHubInstallation } from '../../types/api'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { actionMessage, useWorkspaceData } from './useAsync'
import styles from './Workspace.module.css'

const loadInstallations = () => apiClient.getInstallations()

/**
 * GitHub App installations linked to the selected workspace. Linking is checked server-side
 * against the installations the signed-in user can access on GitHub; incidents of an
 * installation become visible to exactly the workspace that links it.
 */
export const InstallationsSection: React.FC<{ onChange?: () => void }> = ({ onChange }) => {
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const linked = useWorkspaceData(loadInstallations)
  const [available, setAvailable] = useState<GitHubInstallation[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const role = state.workspace?.role
  const manager = role === 'owner' || role === 'admin'

  const act = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key)
    setMessage(null)
    try {
      await action()
      linked.reload()
      onChange?.()
    } catch (reason) {
      setMessage(actionMessage(reason))
    } finally {
      setBusy(null)
    }
  }

  const linkedIds = new Set((linked.data ?? []).map((i) => i.installation_id))

  return (
    <section className={styles.section} aria-labelledby="installations-title">
      <div className={styles.sectionHead}>
        <h2 id="installations-title" className={styles.sectionTitle}>
          GitHub App installations
        </h2>
        {manager && (
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy === 'find'}
            onClick={() => void act('find', async () => setAvailable(await apiClient.getAvailableInstallations()))}
          >
            {busy === 'find' ? 'Checking GitHub…' : 'Find installations'}
          </button>
        )}
      </div>
      {message && (
        <p className={styles.message} role="alert">
          {message}
        </p>
      )}
      {linked.error ? (
        <ApiErrorPanel error={linked.error} onRetry={linked.reload} subject="installations" />
      ) : (linked.data ?? []).length === 0 ? (
        <p className={styles.empty}>
          {linked.loading ? 'Loading…' : 'No installation linked. Incidents appear once one is linked.'}
        </p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Account</th>
              <th scope="col" className={styles.hideNarrow}>Installation</th>
              {manager && <th scope="col"><span className="sr-only">Actions</span></th>}
            </tr>
          </thead>
          <tbody>
            {linked.data?.map((installation) => (
              <tr key={installation.installation_id}>
                <td>
                  <span className="mono">{installation.account_login}</span>{' '}
                  <span className="muted">({installation.account_type})</span>
                </td>
                <td className={`mono ${styles.hideNarrow}`}>{installation.installation_id}</td>
                {manager && (
                  <td>
                    <span className={styles.row}>
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={busy !== null}
                        onClick={() => void act(`sync-${installation.installation_id}`, () => apiClient.syncInstallation(installation.installation_id))}
                      >
                        Sync repositories
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        disabled={busy !== null}
                        onClick={() => void act(`unlink-${installation.installation_id}`, () => apiClient.unlinkInstallation(installation.installation_id))}
                      >
                        Unlink
                      </button>
                    </span>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {available && (
        <div>
          {available.filter((i) => !linkedIds.has(i.installation_id)).length === 0 ? (
            <p className={styles.empty}>
              No other installations of the Akesis GitHub App are available to your GitHub account.
              Install the App on an account or organization first.
            </p>
          ) : (
            <ul className={styles.steps}>
              {available
                .filter((i) => !linkedIds.has(i.installation_id))
                .map((installation) => (
                  <li key={installation.installation_id} className={styles.row}>
                    <span className="mono">{installation.account_login}</span>
                    <span className="muted">({installation.account_type})</span>
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={busy !== null || Boolean(installation.suspended)}
                      onClick={() => void act(`link-${installation.installation_id}`, () => apiClient.linkInstallation(installation.installation_id))}
                    >
                      {installation.suspended ? 'Suspended' : busy === `link-${installation.installation_id}` ? 'Linking…' : 'Link'}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}
      <p className={styles.note}>
        {manager
          ? 'You can link the installation on your own GitHub account. An organization installation can be linked only to a team workspace, by a verified owner of that organization.'
          : 'Owners and admins link installations.'}
      </p>
    </section>
  )
}
