import React, { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiClient, ApiError } from '../../services/api'
import type { IncidentDetail } from '../../types/api'
import { UnifiedDiffViewer } from './UnifiedDiffViewer'
import { DockedTerminal } from './DockedTerminal'
import { IncidentSkeleton } from './IncidentSkeleton'
import { PipelineStageGraph } from '../Common/PipelineStageGraph'
import { StatusBadge } from '../Common/StatusBadge'
import { CopyButton } from '../Common/CopyButton'
import {
  AlertTriangle,
  ArrowLeft,
  Code,
  ExternalLink,
  GitPullRequest,
  RefreshCw,
  Zap,
} from 'lucide-react'

export const IncidentDetailWorkspace: React.FC = () => {
  const { incidentId } = useParams<{ incidentId: string }>()
  const [incident, setIncident] = useState<IncidentDetail | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [showRawJson, setShowRawJson] = useState<boolean>(false)

  const loadIncident = async () => {
    if (!incidentId) return
    setIsLoading(true)
    setError(null)
    try {
      const data = await apiClient.getIncidentDetail(incidentId)
      setIncident(data)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.statusCode === 404) {
          setError(`Incident '${incidentId}' not found.`)
        } else {
          setError(err.message)
        }
      } else {
        setError('Failed to retrieve incident details from backend API.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadIncident()
  }, [incidentId])

  if (isLoading) {
    return <IncidentSkeleton />
  }

  if (error || !incident) {
    return (
      <div className="state-box state-box-error" style={{ minHeight: 240 }}>
        <AlertTriangle size={20} style={{ color: 'var(--status-danger)' }} />
        <div className="state-box-title">Error Loading Incident Workspace</div>
        <div className="state-box-desc">{error || 'Incident data unavailable'}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <Link to="/incidents" className="btn btn-sm">
            <ArrowLeft size={11} />
            <span>Back to Incidents</span>
          </Link>
          <button type="button" className="btn btn-sm" onClick={loadIncident}>
            <RefreshCw size={11} />
            <span>Retry</span>
          </button>
        </div>
      </div>
    )
  }

  const riskLevel = incident.fix_proposal?.estimated_risk_level || incident.fix_proposal?.risk_level
  const logExcerpt = incident.traceback_excerpt || incident.failure_context?.raw_log_excerpt
  const mutationId = (incident as IncidentDetail & { mutation_id?: string | null }).mutation_id ?? null

  return (
    <div className="incident-workspace">
      {/* Compact header: identity, actions, and the topology strip */}
      <header className="ws-header">
        <div className="ws-header-row">
          <div className="ws-title-group" style={{ gap: 8 }}>
            <Link to="/incidents" className="btn btn-sm btn-icon" title="Back to incidents">
              <ArrowLeft size={12} />
            </Link>
            <span className="mono ws-id" title={incident.incident_id} style={{ fontWeight: 600 }}>
              {incident.incident_id}
            </span>
            <CopyButton text={incident.incident_id} label="Incident ID" />
            <span className="ws-sep">/</span>
            <span className="ws-meta-text">{incident.repository}</span>
            <span className="ws-sep">/</span>
            <span className="mono ws-meta-text">{incident.branch}</span>
            <StatusBadge status={incident.current_stage} type="stage" />
          </div>

          <div className="ws-header-actions">
            {incident.pr_url && (
              <a href={incident.pr_url} target="_blank" rel="noopener noreferrer" className="btn btn-sm ws-pr-link">
                <GitPullRequest size={11} />
                <span>PR #{incident.pr_number}</span>
                <ExternalLink size={10} />
              </a>
            )}
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setShowRawJson((prev) => !prev)}
              title="Toggle raw JSON payload"
            >
              <Code size={11} />
              <span>{showRawJson ? 'Hide JSON' : 'JSON'}</span>
            </button>
            <button type="button" className="btn btn-sm btn-icon" onClick={loadIncident} title="Refresh incident">
              <RefreshCw size={11} />
            </button>
          </div>
        </div>

        <div className="ws-pipeline-strip">
          <PipelineStageGraph
            currentStage={incident.current_stage}
            approvalStatus={incident.approval_status}
            prUrl={incident.pr_url}
            prNumber={incident.pr_number}
            proposalId={incident.fix_proposal?.proposal_id}
            validationStatus={incident.validation_result?.status}
            approvalId={incident.approval_id}
            failureReason={incident.failure_message}
            showStatusBanner={true}
          />
        </div>
      </header>

      {/* Raw JSON Context Inspector Drawer */}
      {showRawJson && (
        <div className="ws-raw-json">
          <div className="ws-raw-json-header">
            <span className="ws-label" style={{ marginBottom: 0 }}>Raw IncidentDetail JSON</span>
            <CopyButton text={JSON.stringify(incident, null, 2)} label="Raw JSON" />
          </div>
          <pre className="terminal-view" style={{ maxHeight: 220 }}>
            {JSON.stringify(incident, null, 2)}
          </pre>
        </div>
      )}

      {/* Three regions: context rail, code editor, inspector */}
      <div className="incident-workstation">
        {/* LEFT: context rail */}
        <aside className="workstation-column workstation-left ws-rail">
          <section className="ws-section">
            <h2 className="ws-section-title">CI Context</h2>
            <dl className="ws-kv">
              <dt>Workflow</dt>
              <dd className="ws-truncate" title={incident.workflow_name}>{incident.workflow_name}</dd>
              <dt>Commit</dt>
              <dd className="ws-mono-value">
                <span>{incident.commit_sha.slice(0, 7)}</span>
                <CopyButton text={incident.commit_sha} label="SHA" />
              </dd>
              <dt>Run</dt>
              <dd>
                {incident.failure_context?.run_url ? (
                  <a href={incident.failure_context.run_url} target="_blank" rel="noopener noreferrer" className="mono ws-inline-link">
                    <span>#{incident.run_id}</span>
                    <ExternalLink size={9} />
                  </a>
                ) : (
                  <span className="mono">#{incident.run_id}</span>
                )}
              </dd>
              {incident.target_file && (
                <>
                  <dt>Target</dt>
                  <dd className="mono ws-link-value" title={`${incident.target_file}:${incident.target_line ?? 1}`}>
                    {incident.target_file}:{incident.target_line ?? 1}
                  </dd>
                </>
              )}
            </dl>
          </section>

          <section className="ws-section">
            <h2 className="ws-section-title">Failure Signal</h2>
            <dl className="ws-kv">
              <dt>Error</dt>
              <dd className="ws-error-type">{incident.error_type}</dd>
            </dl>
            <div className="ws-assertion">
              {incident.failure_message || 'No explicit assertion text supplied.'}
            </div>
            {logExcerpt && (
              <div className="ws-log">
                <div className="ws-log-header">
                  <span className="ws-section-subtitle">Cleaned CI Log Excerpt</span>
                  <CopyButton text={logExcerpt} label="Log excerpt" />
                </div>
                <pre className="ws-log-body">{logExcerpt}</pre>
              </div>
            )}
          </section>
        </aside>

        {/* CENTRE: rationale and code editor */}
        <div className="workstation-column workstation-center">
          {incident.fix_proposal ? (
            <div className="ws-rationale">
              <div className="ws-rationale-header">
                <span className="ws-rationale-title">
                  <Zap size={12} style={{ color: 'var(--text-secondary)' }} />
                  Proposed fix
                </span>
                <span className="ws-rationale-meta">
                  <span>Confidence {(incident.fix_proposal.confidence_score * 100).toFixed(0)}%</span>
                  <span className="ws-sep">·</span>
                  <span>Risk {(riskLevel || 'low').toUpperCase()}</span>
                </span>
              </div>
              <p className="ws-body-text">
                {incident.fix_proposal.explanation || incident.fix_proposal.rationale}
              </p>
              {incident.fix_proposal.risk_assessment && (
                <p className="ws-muted-text">
                  <strong>Risk assessment:</strong> {incident.fix_proposal.risk_assessment}
                </p>
              )}
            </div>
          ) : (
            <div className="ws-muted-text ws-empty-note">No fix proposal generated for this incident.</div>
          )}

          <UnifiedDiffViewer
            diff={incident.diff_content || incident.fix_proposal?.unified_diff}
            filePath={incident.target_file}
          />
        </div>

        {/* RIGHT: inspector */}
        <aside className="workstation-column workstation-right ws-rail">
          <section className="ws-section">
            <div className="ws-section-head">
              <h2 className="ws-section-title">Inspector</h2>
              <StatusBadge status={incident.category} type="category" />
            </div>
            <dl className="ws-kv ws-kv-rows">
              <dt>Pipeline</dt>
              <dd><StatusBadge status={incident.current_stage} type="stage" /></dd>
              <dt>Approval</dt>
              <dd><StatusBadge status={incident.approval_status} type="approval" /></dd>
              <dt>Approval ID</dt>
              <dd>
                {incident.approval_id ? (
                  <span className="mono ws-truncate" title={incident.approval_id} aria-label={`Approval ID ${incident.approval_id}`}>
                    {incident.approval_id}
                  </span>
                ) : (
                  <span className="ws-muted-text">Not recorded</span>
                )}
              </dd>
              <dt>Validation</dt>
              <dd>
                {incident.validation_result?.status ? (
                  <StatusBadge status={incident.validation_result.status} type="validation" />
                ) : (
                  <span className="ws-muted-text">—</span>
                )}
              </dd>
              <dt>Mutation ID</dt>
              <dd>
                {mutationId ? (
                  <span className="mono ws-truncate" title={mutationId}>{mutationId}</span>
                ) : (
                  <span className="ws-muted-text">Not recorded</span>
                )}
              </dd>
              <dt>Pull request</dt>
              <dd>
                {incident.pr_url ? (
                  <a href={incident.pr_url} target="_blank" rel="noopener noreferrer" className="ws-inline-link">
                    <span>PR #{incident.pr_number}</span>
                    <ExternalLink size={9} />
                  </a>
                ) : (
                  <span className="ws-muted-text">Not created</span>
                )}
              </dd>
              <dt>Ingested</dt>
              <dd className="mono">{new Date(incident.created_at).toLocaleTimeString()}</dd>
              <dt>Updated</dt>
              <dd className="mono">{new Date(incident.updated_at).toLocaleTimeString()}</dd>
            </dl>
          </section>
        </aside>
      </div>

      {/* Docked terminal, full width across the bottom of the workspace */}
      <DockedTerminal incident={incident} />
    </div>
  )
}
