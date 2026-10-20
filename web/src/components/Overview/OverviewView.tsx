import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient, ApiError } from '../../services/api'
import type { IncidentSummary, PipelineSummary, ReadinessResponse } from '../../types/api'
import { StatusBadge } from '../Common/StatusBadge'
import { CopyButton } from '../Common/CopyButton'
import { TableSkeleton } from '../Common/Skeleton'
import { AlertTriangle } from 'lucide-react'

const RECENT_LIMIT = 8

export const OverviewView: React.FC = () => {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadData = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [readinessRes, incidentsRes, pipelinesRes] = await Promise.all([
        apiClient.getReadiness().catch((err) => {
          console.error('Readiness check error:', err)
          return null
        }),
        apiClient.getIncidents({ limit: 10, offset: 0 }).catch((err) => {
          console.error('Incidents load error:', err)
          return []
        }),
        apiClient.getPipelines({ limit: 10, offset: 0 }).catch((err) => {
          console.error('Pipelines load error:', err)
          return []
        }),
      ])

      setReadiness(readinessRes)
      setIncidents(incidentsRes)
      setPipelines(pipelinesRes)
      setLastUpdated(new Date())
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Failed to fetch operational status from backend')
      }
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  // Derived metrics from loaded records ONLY
  const loadedIncidentCount = incidents.length
  const loadedPipelineCount = pipelines.length
  const pendingApprovalsCount = incidents.filter(
    (inc) => inc.approval_status === 'pending' || inc.current_stage === 'awaiting_approval'
  ).length

  const readinessTone =
    readiness?.status === 'ready' ? 'is-ok' : readiness ? 'is-warn' : 'is-fail'
  const readinessLabel = readiness?.status === 'ready' ? 'READY' : readiness ? readiness.status.toUpperCase() : 'UNREACHABLE'

  return (
    <div className="overview-view">
      {error && (
        <div className="ov-notice" role="alert">
          <AlertTriangle size={14} />
          <div>
            <div className="ov-notice-title">Backend connection notice</div>
            <div className="ov-notice-desc">{error}</div>
          </div>
        </div>
      )}

      {/* Control-plane header: service state and loaded counts, stated plainly */}
      <header className="ov-header">
        <div className="ov-header-title">
          <h1>Overview</h1>
          <span className="ov-header-sub">Remediation control plane</span>
        </div>

        <dl className="ov-stats" aria-label="System Operational Strip">
          <div className="ov-stat">
            <dt>API Readiness:</dt>
            <dd>
              <span className={`ov-state ${readinessTone}`}>
                <span className="ov-dot" aria-hidden="true" />
                {readinessLabel}
              </span>
              {readiness?.environment && <span className="ov-env mono">{readiness.environment}</span>}
            </dd>
          </div>
          <div className="ov-stat">
            <dt>Loaded Incidents:</dt>
            <dd className="ov-num">{isLoading ? '—' : loadedIncidentCount}</dd>
          </div>
          <div className="ov-stat">
            <dt>Pending Approvals:</dt>
            <dd className={`ov-num ${pendingApprovalsCount > 0 ? 'is-warn' : ''}`}>
              {isLoading ? '—' : pendingApprovalsCount}
            </dd>
          </div>
          <div className="ov-stat">
            <dt>Loaded Pipelines:</dt>
            <dd className="ov-num">{isLoading ? '—' : loadedPipelineCount}</dd>
          </div>
          <div className="ov-stat ov-stat-probe">
            <dt>Last probe</dt>
            <dd className="mono">{lastUpdated ? lastUpdated.toLocaleTimeString() : 'probing…'}</dd>
          </div>
        </dl>
      </header>

      <div className="ov-grid">
        {/* Incidents */}
        <section className="ov-section" aria-labelledby="ov-incidents-title">
          <div className="ov-section-head">
            <h2 id="ov-incidents-title">Recent incidents</h2>
            <Link to="/incidents" className="ov-link">View all</Link>
          </div>

          {isLoading ? (
            <TableSkeleton label="Loading incident queue" columns={[3, 3, 1, 1]} rows={6} />
          ) : incidents.length === 0 ? (
            <div className="ov-empty">No incidents recorded in database.</div>
          ) : (
            <div className="ov-table-wrap">
              <table className="ov-table">
                <thead>
                  <tr>
                    <th scope="col">Incident</th>
                    <th scope="col">Repository</th>
                    <th scope="col">Category</th>
                    <th scope="col">Stage</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.slice(0, RECENT_LIMIT).map((inc) => (
                    <tr key={inc.incident_id}>
                      <td>
                        <div className="ov-cell-id">
                          <Link to={`/incidents/${inc.incident_id}`} className="mono ov-id">
                            {inc.incident_id.slice(0, 12)}
                          </Link>
                          <CopyButton text={inc.incident_id} label="Incident ID" />
                        </div>
                      </td>
                      <td>
                        <div className="ov-repo">{inc.repository}</div>
                        <div className="ov-sub">
                          {inc.workflow_name} · <span className="mono">{inc.branch}</span>
                        </div>
                      </td>
                      <td>
                        <StatusBadge status={inc.category} type="category" />
                      </td>
                      <td>
                        <StatusBadge status={inc.current_stage} type="stage" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Pipelines */}
        <section className="ov-section" aria-labelledby="ov-pipelines-title">
          <div className="ov-section-head">
            <h2 id="ov-pipelines-title">Recent pipelines</h2>
            <Link to="/pipelines" className="ov-link">View all</Link>
          </div>

          {isLoading ? (
            <TableSkeleton label="Loading pipeline ledger" columns={[3, 2, 2, 2]} rows={6} />
          ) : pipelines.length === 0 ? (
            <div className="ov-empty">No orchestration pipelines recorded.</div>
          ) : (
            <div className="ov-table-wrap">
              <table className="ov-table">
                <thead>
                  <tr>
                    <th scope="col">Pipeline</th>
                    <th scope="col">Incident</th>
                    <th scope="col">Status</th>
                    <th scope="col">SHA</th>
                  </tr>
                </thead>
                <tbody>
                  {pipelines.slice(0, RECENT_LIMIT).map((pipe) => (
                    <tr key={pipe.pipeline_id}>
                      <td>
                        <div className="ov-cell-id">
                          <span className="mono ov-id-plain">{pipe.pipeline_id.slice(0, 14)}</span>
                          <CopyButton text={pipe.pipeline_id} label="Pipeline ID" />
                        </div>
                      </td>
                      <td>
                        <Link to={`/incidents/${pipe.incident_id}`} className="mono ov-sub-link">
                          {pipe.incident_id.slice(0, 10)}
                        </Link>
                      </td>
                      <td>
                        <StatusBadge status={pipe.status} type="pipeline" />
                      </td>
                      <td>
                        <div className="ov-cell-id">
                          <span className="mono ov-sha">{pipe.commit_sha.slice(0, 7)}</span>
                          <CopyButton text={pipe.commit_sha} label="SHA" />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
