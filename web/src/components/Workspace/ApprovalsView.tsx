import React from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../../services/api'
import { appPath } from '../../lib/routes'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { useWorkspaceData } from './useAsync'
import styles from './Workspace.module.css'

const loadPending = async () =>
  (await apiClient.getIncidents({ limit: 100 })).filter((incident) => incident.approval_status === 'pending')

/**
 * Remediations waiting for a human decision in this workspace. Read-only by design: Slack is the
 * only approval channel, and the decision is re-authorized against the approver's workspace role
 * when it is recorded and again when the remediation runs.
 */
export const ApprovalsView: React.FC = () => {
  const { data, error, loading, reload } = useWorkspaceData(loadPending)
  return (
    <div className={styles.view}>
      <header className={styles.head}>
        <h1 className={styles.title}>Approvals</h1>
        <p className={styles.sub}>
          Decisions are made in Slack by mapped approvers. This page shows what is waiting; it
          cannot approve anything.
        </p>
      </header>
      <section className={styles.section} aria-labelledby="pending-title">
        <div className={styles.sectionHead}>
          <h2 id="pending-title" className={styles.sectionTitle}>
            Waiting for approval
          </h2>
          <button type="button" className="btn btn-sm" onClick={reload} disabled={loading}>
            Refresh
          </button>
        </div>
        {error ? (
          <ApiErrorPanel error={error} onRetry={reload} subject="approvals" />
        ) : (data ?? []).length === 0 ? (
          <p className={styles.empty}>{loading ? 'Loading…' : 'Nothing is waiting for approval.'}</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Incident</th>
                <th scope="col">Repository</th>
                <th scope="col" className={styles.hideNarrow}>Failure</th>
              </tr>
            </thead>
            <tbody>
              {data?.map((incident) => (
                <tr key={incident.incident_id}>
                  <td>
                    <Link className="ws-inline-link mono" to={appPath(`/incidents/${incident.incident_id}`)}>
                      {incident.incident_id}
                    </Link>
                  </td>
                  <td className="mono">{incident.repository}</td>
                  <td className={styles.hideNarrow}>{incident.failure_message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
