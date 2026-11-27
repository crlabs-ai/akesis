import React, { useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { apiClient, refreshSession } from '../../services/api'
import { session } from '../../services/session'
import { appPath } from '../../lib/routes'
import { InstallationsSection } from './InstallationsSection'
import { actionMessage, useWorkspaceData } from './useAsync'
import styles from './Workspace.module.css'

const loadOnboarding = async () => {
  const [installations, invitations] = await Promise.all([
    apiClient.getInstallations(),
    apiClient.getMyInvitations(),
  ])
  return { installations, invitations }
}

const Step: React.FC<{ n: number; done: boolean; title: string; children?: React.ReactNode }> = ({
  n,
  done,
  title,
  children,
}) => (
  <li className={styles.step}>
    <span className={`${styles.stepMark} ${done ? styles.stepDone : ''}`} aria-hidden="true">
      {done ? '✓' : n}
    </span>
    <div className={styles.stepBody}>
      <strong>
        {title}
        <span className="sr-only">{done ? ' (done)' : ' (to do)'}</span>
      </strong>
      {children}
    </div>
  </li>
)

/** First-run checklist. Every step reflects real backend state; nothing is pre-ticked. */
export const OnboardingView: React.FC = () => {
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const { data, reload } = useWorkspaceData(loadOnboarding)
  const [message, setMessage] = useState<string | null>(null)
  if (!state.me || !state.workspace) return null
  const linked = (data?.installations ?? []).length > 0

  return (
    <div className={styles.view}>
      <header className={styles.head}>
        <h1 className={styles.title}>Welcome, {state.me.display_name ?? state.me.github_login}</h1>
        <p className={styles.sub}>Three steps connect Akesis to your CI. Remediation never merges on its own.</p>
      </header>
      {message && (
        <p className={styles.message} role="alert">
          {message}
        </p>
      )}
      <ol className={styles.steps}>
        <Step n={1} done title="Signed in with GitHub">
          <p className={styles.note}>
            Your personal workspace “{state.workspace.name}” is ready. Create a team workspace in{' '}
            <Link className="ws-inline-link" to={appPath('/settings')}>Settings</Link> to work with others.
          </p>
        </Step>
        <Step n={2} done={linked} title="Link the Akesis GitHub App installation">
          <p className={styles.note}>
            Incidents from an installation appear only in the workspace that links it. Install the
            App on your account or organization on GitHub first, then link it here.
          </p>
        </Step>
        <Step n={3} done={false} title="Map approvers to Slack">
          <p className={styles.note}>
            Approvals happen only in Slack. A workspace owner maps and attests each approver’s Slack user ID on{' '}
            <Link className="ws-inline-link" to={appPath('/members')}>Members</Link>.
          </p>
        </Step>
      </ol>

      <InstallationsSection onChange={reload} />

      {(data?.invitations ?? []).length > 0 && (
        <section className={styles.section} aria-labelledby="invites-title">
          <h2 id="invites-title" className={styles.sectionTitle}>
            Invitations for you
          </h2>
          <ul className={styles.steps}>
            {data?.invitations.map((invitation) => (
              <li key={invitation.invitation_id} className={styles.row}>
                <span>
                  {invitation.workspace_name} as <strong>{invitation.role}</strong>
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() =>
                    void (async () => {
                      setMessage(null)
                      try {
                        const joined = await apiClient.acceptInvitation(invitation.invitation_id)
                        await refreshSession()
                        session.selectWorkspace(joined.workspace_id)
                      } catch (reason) {
                        setMessage(actionMessage(reason))
                      }
                    })()
                  }
                >
                  Accept
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
