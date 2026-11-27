import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { GitHubMark } from '../site/icons'
import { Button, Logo } from '../design'
import { signInUrl } from '../services/api'
import styles from './Auth.module.css'

/** Messages for the error codes the backend and callback page send here. Never raw detail. */
const ERRORS: Record<string, string> = {
  access_denied: 'GitHub sign-in was cancelled. Nothing was shared with Akesis.',
  invalid_state: 'That sign-in link expired or was opened in another browser. Start again here.',
  code_exchange_failed: 'GitHub did not accept the sign-in. Start again.',
  github_profile_unavailable: 'GitHub could not be reached. Try again in a moment.',
  github_profile_invalid: 'GitHub returned an unexpected profile. Try again.',
  sign_in_not_configured:
    'GitHub sign-in is not configured on this server yet. An administrator must set the GitHub App client ID and secret.',
  session_expired: 'Your session expired. Sign in again.',
  session_revoked: 'You were signed out. Sign in again.',
}

export default function LoginPage() {
  const [params] = useSearchParams()
  const error = params.get('error')
  const returnTo = params.get('return_to') ?? undefined
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    document.title = 'Sign in · Akesis'
  }, [])

  return (
    <div className="ak">
      <main className={styles.page}>
        <section className={styles.panel} aria-labelledby="login-title">
          <Link to="/" className={styles.brand} aria-label="Akesis home">
            <Logo />
          </Link>
          <h1 id="login-title" className={styles.title}>
            Sign in to Akesis
          </h1>
          <p className={styles.lede}>Use your GitHub account. Your workspace is ready on first sign-in.</p>
          {error && (
            <p className={styles.alert} role="alert">
              {ERRORS[error] ?? 'Sign-in did not complete. Start again.'}
            </p>
          )}
          <div className={styles.actions}>
            {/* A full-page navigation: the backend sets the browser-binding cookie and redirects
                to GitHub. No token is ever handled by this page. */}
            <Button
              size="lg"
              leadingIcon={<GitHubMark size={18} />}
              loading={leaving}
              onClick={() => {
                setLeaving(true)
                window.location.assign(signInUrl(returnTo))
              }}
            >
              {leaving ? 'Redirecting to GitHub…' : 'Continue with GitHub'}
            </Button>
          </div>
          <p className={styles.note}>
            Akesis reads your GitHub identity and the App installations you can access. It never
            merges code: every fix waits for a human approval in Slack.
          </p>
        </section>
      </main>
    </div>
  )
}
