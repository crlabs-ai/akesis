import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Logo } from '../design'
import { ApiError, apiClient, refreshSession } from '../services/api'
import styles from './Auth.module.css'

/**
 * GitHub redirects here with `code` and `state`. They go to the backend once, which checks the
 * state against this browser's binding cookie, exchanges the code with PKCE and sets the
 * HttpOnly session cookie. This page never sees a token.
 */
export default function AuthCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [failure, setFailure] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    document.title = 'Signing in · Akesis'
    if (started.current) return // StrictMode runs effects twice; the state is single use
    started.current = true
    const code = params.get('code')
    const state = params.get('state')
    const providerError = params.get('error')
    if (providerError || !code || !state) {
      navigate(`/login?error=${providerError === 'access_denied' ? 'access_denied' : 'invalid_state'}`, {
        replace: true,
      })
      return
    }
    void (async () => {
      try {
        const { return_to } = await apiClient.completeSignIn(code, state)
        await refreshSession()
        let next = return_to
        if (return_to === '/app') {
          // First stop for a workspace with nothing linked yet: onboarding.
          const linked = await apiClient.getInstallations().catch(() => [])
          if (linked.length === 0) next = '/app/onboarding'
        }
        navigate(next, { replace: true })
      } catch (error) {
        const code = error instanceof ApiError ? error.message : 'invalid_state'
        if (/^[a-z_]+$/.test(code)) {
          navigate(`/login?error=${code}`, { replace: true })
        } else {
          setFailure('Sign-in could not be completed. The backend may be unavailable.')
        }
      }
    })()
  }, [navigate, params])

  return (
    <div className="ak">
      <main className={styles.page}>
        <section className={styles.panel} aria-labelledby="callback-title" aria-busy={!failure}>
          <span className={styles.brand}>
            <Logo />
          </span>
          <h1 id="callback-title" className={styles.title}>
            {failure ? 'Sign-in failed' : 'Signing you in'}
          </h1>
          {failure ? (
            <>
              <p className={styles.alert} role="alert">
                {failure}
              </p>
              <Link to="/login" className={styles.back}>
                Back to sign in
              </Link>
            </>
          ) : (
            <p className={styles.status} role="status">
              <Loader2 size={16} className={styles.spin} aria-hidden="true" />
              Verifying with GitHub…
            </p>
          )}
        </section>
      </main>
    </div>
  )
}
