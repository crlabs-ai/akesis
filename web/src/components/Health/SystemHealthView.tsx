import React, { useEffect, useState } from 'react'
import { apiClient, ApiError } from '../../services/api'
import type { LivenessResponse, ReadinessResponse } from '../../types/api'
import { CopyButton } from '../Common/CopyButton'
import { BlockSkeleton } from '../Common/Skeleton'
import {
  Activity,
  CheckCircle2,
  Clock,
  Code,
  HelpCircle,
  RefreshCw,
  Server,
  ShieldAlert,
  XCircle,
} from 'lucide-react'

export const SystemHealthView: React.FC = () => {
  const [liveness, setLiveness] = useState<LivenessResponse | null>(null)
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [showRawJson, setShowRawJson] = useState<boolean>(false)

  const checkHealth = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [livenessRes, readinessRes] = await Promise.all([
        apiClient.getLiveness().catch((err) => {
          throw new Error(`Liveness probe failed: ${err.message}`)
        }),
        apiClient.getReadiness().catch((err) => {
          throw new Error(`Readiness probe failed: ${err.message}`)
        }),
      ])

      setLiveness(livenessRes)
      setReadiness(readinessRes)
      setLastChecked(new Date())
    } catch (err) {
      // A failed probe clears the previous values. Old results must never read as current.
      setLiveness(null)
      setReadiness(null)
      if (err instanceof ApiError) {
        setError(err.message)
      } else if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Unknown system health communication error')
      }
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    checkHealth()
  }, [])

  return (
    <div className="system-health-view">
      {/* Header Panel */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">
          <div>
            <div className="panel-title">
              <Activity size={16} style={{ color: 'var(--status-info)' }} />
              <span>System Health & Diagnostics</span>
            </div>
            <div className="panel-subtitle">
              Backend probes: GET /health/liveness (process responding). GET /health/readiness returns a fixed
              value and does not check the database. Neither probe reports the retry worker.
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setShowRawJson((prev) => !prev)}
            >
              <Code size={13} />
              <span>{showRawJson ? 'Hide Payloads' : 'Inspect Payloads'}</span>
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={checkHealth}
              disabled={isLoading}
              title="Run health check"
            >
              <RefreshCw size={13} className={isLoading ? 'spinner' : ''} />
              <span>{isLoading ? 'Checking...' : 'Check Health'}</span>
            </button>
          </div>
        </div>

        <div className="panel-body" style={{ padding: '14px 16px', backgroundColor: 'var(--bg-panel-subtle)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.8125rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Clock size={14} style={{ color: 'var(--text-muted)' }} />
              <span style={{ color: 'var(--text-secondary)' }}>
                {error
                  ? `Last probe failed${lastChecked ? `. Last successful probe: ${lastChecked.toLocaleTimeString()}` : ''}`
                  : lastChecked
                    ? `Last probed at: ${lastChecked.toLocaleTimeString()} (${lastChecked.toLocaleDateString()})`
                    : 'Probing backend...'}
              </span>
            </div>
            {error ? (
              <span className="badge badge-danger">
                <XCircle size={11} /> Probe failed
              </span>
            ) : lastChecked ? (
              <span className="badge badge-success">
                <CheckCircle2 size={11} /> Probes Active
              </span>
            ) : null}
          </div>
        </div>
      </div>

      {isLoading && !lastChecked && !error && (
        <BlockSkeleton lines={3} label="Probing backend health" />
      )}
      {error && (
        <div className="state-box state-box-error" style={{ marginBottom: 16 }}>
          <ShieldAlert size={28} style={{ color: 'var(--status-danger)' }} />
          <div className="state-box-title">Health Check Failure</div>
          <div className="state-box-desc">{error}</div>
          <button type="button" className="btn btn-sm" onClick={checkHealth} style={{ marginTop: 8 }}>
            Retry Check
          </button>
        </div>
      )}

      {/* Raw JSON Payloads */}
      {showRawJson && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">Raw Health Probe Responses</div>
            <CopyButton
              text={JSON.stringify({ liveness, readiness }, null, 2)}
              label="Health JSON"
            />
          </div>
          <div className="panel-body">
            <pre className="terminal-view">
              {JSON.stringify({ liveness, readiness }, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Probes Detail Grid */}
      <div className="grid-2">
        {/* Liveness Probe Card */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Server size={16} />
              <span>API Liveness (/health/liveness)</span>
            </div>
            {liveness?.status === 'ok' ? (
              <span className="badge badge-success">
                <CheckCircle2 size={11} /> OK
              </span>
            ) : liveness ? (
              <span className="badge badge-warning">{liveness.status.toUpperCase()}</span>
            ) : (
              <span className="badge badge-danger">
                <XCircle size={11} /> DOWN
              </span>
            )}
          </div>
          <div className="panel-body">
            <div style={{ fontSize: '0.8125rem', lineHeight: 1.6 }}>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: 'var(--text-muted)' }}>Probe Endpoint: </span>
                <span className="mono">/health/liveness</span>
              </div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: 'var(--text-muted)' }}>Status Received: </span>
                <span className="mono" style={{ fontWeight: 600 }}>
                  {liveness?.status || (isLoading ? 'Probing…' : '(No response)')}
                </span>
              </div>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', marginTop: 12 }}>
                Liveness confirms that the FastAPI web server process is running and accepting HTTP socket connections.
              </p>
            </div>
          </div>
        </div>

        {/* Readiness Probe Card */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Activity size={16} />
              <span>API Readiness (/health/readiness)</span>
            </div>
            {readiness?.status === 'ready' ? (
              <span className="badge badge-success">
                <CheckCircle2 size={11} /> READY
              </span>
            ) : readiness ? (
              <span className="badge badge-warning">{readiness.status.toUpperCase()}</span>
            ) : (
              <span className="badge badge-danger">
                <XCircle size={11} /> NOT READY
              </span>
            )}
          </div>
          <div className="panel-body">
            <div style={{ fontSize: '0.8125rem', lineHeight: 1.6 }}>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: 'var(--text-muted)' }}>Probe Endpoint: </span>
                <span className="mono">/health/readiness</span>
              </div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: 'var(--text-muted)' }}>Readiness Status: </span>
                <span className="mono" style={{ fontWeight: 600 }}>
                  {readiness?.status || (isLoading ? 'Probing…' : '(No response)')}
                </span>
              </div>
              <div style={{ marginBottom: 8 }}>
                <span style={{ color: 'var(--text-muted)' }}>Reported Environment: </span>
                <span className="mono">{readiness?.environment || 'Not provided'}</span>
              </div>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', marginTop: 12 }}>
                Readiness returns a fixed value from the process. It does not check the database, so a ready response does not confirm that the database is available.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* System Boundaries & Isolation Transparency Panel */}
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-header">
          <div className="panel-title">
            <HelpCircle size={16} style={{ color: 'var(--text-muted)' }} />
            <span>Unexposed Integrations & Boundary Status</span>
          </div>
        </div>
        <div className="panel-body">
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: 12 }}>
            The following are not reported by this console. Nothing here is inferred from them:
          </p>
          <ul style={{ fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.8, paddingLeft: 18 }}>
            <li>
              <strong>Retry worker:</strong> Not reported by the API. Its health is checked only by a host-side command, so this page cannot show whether it is running.
            </li>
            <li>
              <strong>PostgreSQL database:</strong> Not reported by any endpoint this console calls, so its state is not shown.
            </li>
            <li>
              <strong>Sandbox validation:</strong> Not reported by any endpoint this console calls. No sandbox status is shown.
            </li>
            <li>
              <strong>GitHub credentials:</strong> Not sent to or stored in this browser. Only the operator read token is held here, for the session.
            </li>
            <li>
              <strong>Slack:</strong> Not reported by any endpoint this console calls.
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}
