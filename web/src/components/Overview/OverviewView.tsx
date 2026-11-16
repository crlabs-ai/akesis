import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../../services/api'
import { useTokenChangeReload } from '../../hooks/useTokenChangeReload'
import type { IncidentSummary, PipelineSummary, ReadinessResponse } from '../../types/api'
import { StatusBadge } from '../Common/StatusBadge'
import { CopyButton } from '../Common/CopyButton'
import { TableSkeleton } from '../Common/Skeleton'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { Info } from 'lucide-react'

/** Size of each list on this page. Counts shown here are page sizes, never totals. */
const RECENT_LIMIT = 8

export const OverviewView: React.FC = () => {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [readinessError, setReadinessError] = useState<unknown>(null)
  const [incidentsError, setIncidentsError] = useState<unknown>(null)
  const [pipelinesError, setPipelinesError] = useState<unknown>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  // Each source settles independently. A failed source shows its own error state; it never
  // becomes an empty list, and it never hides the other sources.
  const loadData = useCallback(async () => {
    setIsLoading(true)
    const [readinessResult, incidentsResult, pipelinesResult] = await Promise.allSettled([
      apiClient.getReadiness(),
      apiClient.getIncidents({ limit: RECENT_LIMIT, offset: 0 }),
      apiClient.getPipelines({ limit: RECENT_LIMIT, offset: 0 }),
    ])

    if (readinessResult.status === 'fulfilled') {
      setReadiness(readinessResult.value)
      setReadinessError(null)
    } else {
      setReadiness(null)
      setReadinessError(readinessResult.reason)
    }

    if (incidentsResult.status === 'fulfilled') {
      setIncidents(incidentsResult.value)
      setIncidentsError(null)
    } else {
      setIncidents([])
      setIncidentsError(incidentsResult.reason)
    }

    if (pipelinesResult.status === 'fulfilled') {
      setPipelines(pipelinesResult.value)
      setPipelinesError(null)
    } else {
      setPipelines([])
      setPipelinesError(pipelinesResult.reason)
    }

    setLastUpdated(new Date())
    setIsLoading(false)
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])
  useTokenChangeReload(() => void loadData())

  const probeLabel =
    readinessError !== null
      ? 'UNREACHABLE'
      : readiness?.status === 'ready'
        ? 'REPORTS READY'
        : readiness
          ? readiness.status.toUpperCase()
          : '—'
  const probeTone =
    readinessError !== null ? 'is-fail' : readiness?.status === 'ready' ? 'is-ok' : 'is-warn'

  return (
    <div className="overview-view">
      <div className="hold-banner" role="note">
        <strong>Remediation disabled (ADR-0010 hold).</strong> Verified webhook deliveries are recorded. No fix
        is generated, approved or merged, and no pull request is created. This is the policy in force, not a
        live status reading.
      </div>

      <header className="ov-header">
        <div className="ov-header-title">
          <h1>Overview</h1>
          <span className="ov-header-sub">Remediation control plane</span>
        </div>

        <dl className="ov-stats" aria-label="System Operational Strip">
          <div className="ov-stat">
            <dt>API probe:</dt>
            <dd>
              <span className={`ov-state ${probeTone}`}>
                <span className="ov-dot" aria-hidden="true" />
                {probeLabel}
              </span>
              <span className="ov-env">process only · database not checked</span>
            </dd>
          </div>
          <div className="ov-stat">
            <dt>Incidents shown (newest {RECENT_LIMIT}):</dt>
            <dd className="ov-num">{isLoading ? '—' : incidentsError !== null ? 'n/a' : incidents.length}</dd>
          </div>
          <div className="ov-stat">
            <dt>Pipelines shown (newest {RECENT_LIMIT}):</dt>
            <dd className="ov-num">{isLoading ? '—' : pipelinesError !== null ? 'n/a' : pipelines.length}</dd>
          </div>
          <div className="ov-stat ov-stat-probe">
            <dt>Last probe</dt>
            <dd className="mono">{lastUpdated ? lastUpdated.toLocaleTimeString() : 'probing…'}</dd>
          </div>
        </dl>
        <p className="ov-note">
          <Info size={12} aria-hidden="true" /> These are page sizes. The backend does not provide totals, so this
          page never states one.{' '}
          <Link to="/deliveries" className="ov-link">
            Open webhook deliveries
          </Link>
        </p>
      </header>

      {readinessError !== null && (
        <ApiErrorPanel error={readinessError} subject="API probe" onRetry={() => void loadData()} />
      )}

      <div className="ov-grid">
        {/* Incidents */}
        <section className="ov-section" aria-labelledby="ov-incidents-title">
          <div className="ov-section-head">
            <h2 id="ov-incidents-title">Recent incidents</h2>
            <Link to="/incidents" className="ov-link">View all</Link>
          </div>

          {isLoading ? (
            <TableSkeleton label="Loading incident queue" columns={[3, 3, 1, 1]} rows={6} />
          ) : incidentsError !== null ? (
            <ApiErrorPanel error={incidentsError} subject="incidents" onRetry={() => void loadData()} />
          ) : incidents.length === 0 ? (
            <div className="ov-empty">The backend returned no incidents.</div>
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
                  {incidents.map((inc) => (
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
          ) : pipelinesError !== null ? (
            <ApiErrorPanel error={pipelinesError} subject="pipelines" onRetry={() => void loadData()} />
          ) : pipelines.length === 0 ? (
            <div className="ov-empty">The backend returned no pipelines.</div>
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
                  {pipelines.map((pipe) => (
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
