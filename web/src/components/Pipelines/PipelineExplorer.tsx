import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient, ApiError } from '../../services/api'
import type { PipelineSummary } from '../../types/api'
import { PipelineStageGraph } from '../Common/PipelineStageGraph'
import { StatusBadge } from '../Common/StatusBadge'
import { CopyButton } from '../Common/CopyButton'
import { TableSkeleton } from '../Common/Skeleton'
import { AlertTriangle, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Search } from 'lucide-react'

export const PipelineExplorer: React.FC = () => {
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [selectedPipeline, setSelectedPipeline] = useState<PipelineSummary | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState<number>(0)
  const [searchQuery, setSearchQuery] = useState<string>('')
  const pageSize = 20

  const loadPipelines = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await apiClient.getPipelines({ limit: pageSize, offset: page * pageSize })
      setPipelines(data)
      if (data.length > 0 && !selectedPipeline) {
        setSelectedPipeline(data[0])
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Unable to load pipelines from backend API')
      }
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadPipelines()
  }, [page])

  const filteredPipelines = pipelines.filter((pipe) => {
    if (searchQuery.trim() !== '') {
      const q = searchQuery.toLowerCase()
      const matchesPipeId = pipe.pipeline_id.toLowerCase().includes(q)
      const matchesIncId = pipe.incident_id.toLowerCase().includes(q)
      const matchesOwner = pipe.repository_owner.toLowerCase().includes(q)
      const matchesRepo = pipe.repository_name.toLowerCase().includes(q)
      const matchesSha = pipe.commit_sha.toLowerCase().includes(q)
      const matchesStatus = pipe.status.toLowerCase().includes(q)
      if (!matchesPipeId && !matchesIncId && !matchesOwner && !matchesRepo && !matchesSha && !matchesStatus) {
        return false
      }
    }
    return true
  })

  return (
    <div className="pl-view">
      {/* Toolbar: search, counts and refresh */}
      <div className="pl-toolbar">
        <div className="pl-search">
          <Search size={13} aria-hidden="true" />
          <input
            type="text"
            placeholder="Filter by pipeline, incident, repository, SHA or status"
            className="input-text pl-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search pipelines"
          />
        </div>

        <div className="pl-toolbar-end">
          <span className="pl-count">
            Showing {filteredPipelines.length} of {pipelines.length} loaded records · page {page + 1}
          </span>
          <button
            type="button"
            className="btn btn-sm btn-icon"
            onClick={loadPipelines}
            disabled={isLoading}
            title="Refresh pipelines"
            aria-label="Refresh pipelines"
          >
            <RefreshCw size={13} className={isLoading ? 'spinner' : ''} />
          </button>
        </div>
      </div>

      {/* Selected pipeline: slim detail rail, not a large card */}
      {selectedPipeline && (
        <section className="pl-detail" aria-label="Selected pipeline">
          <div className="pl-detail-head">
            <div className="pl-detail-id">
              <span className="mono pl-detail-title">{selectedPipeline.pipeline_id}</span>
              <CopyButton text={selectedPipeline.pipeline_id} label="Pipeline ID" />
              <StatusBadge status={selectedPipeline.status} type="pipeline" />
            </div>
            <Link to={`/incidents/${selectedPipeline.incident_id}`} className="btn btn-sm">
              <span>Open incident</span>
              <ArrowIcon />
            </Link>
          </div>

          <div className="pl-detail-stages">
            <PipelineStageGraph
              pipelineStatus={selectedPipeline.status}
              prUrl={selectedPipeline.pr_url}
              prNumber={selectedPipeline.pr_number}
              diagnosisId={selectedPipeline.diagnosis_id}
              proposalId={selectedPipeline.proposal_id}
              approvalId={selectedPipeline.approval_id}
              mutationId={selectedPipeline.mutation_id}
              failureReason={selectedPipeline.failure_reason}
              showStatusBanner={true}
            />
          </div>

          <dl className="pl-detail-facts">
            <div>
              <dt>Repository</dt>
              <dd className="pl-truncate" title={`${selectedPipeline.repository_owner}/${selectedPipeline.repository_name}`}>
                {selectedPipeline.repository_owner}/{selectedPipeline.repository_name}
              </dd>
            </div>
            <div>
              <dt>Commit</dt>
              <dd className="pl-inline">
                <span className="mono">{selectedPipeline.commit_sha.slice(0, 7)}</span>
                <CopyButton text={selectedPipeline.commit_sha} label="SHA" />
              </dd>
            </div>
            <div>
              <dt>Run</dt>
              <dd className="mono">#{selectedPipeline.run_id}</dd>
            </div>
            <div>
              <dt>Diagnosis</dt>
              <dd className="pl-inline">
                {selectedPipeline.diagnosis_id ? (
                  <>
                    <span className="mono pl-truncate" title={selectedPipeline.diagnosis_id}>{selectedPipeline.diagnosis_id}</span>
                    <CopyButton text={selectedPipeline.diagnosis_id} label="Diagnosis ID" />
                  </>
                ) : (
                  <span className="pl-muted">None</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Proposal</dt>
              <dd className="pl-inline">
                {selectedPipeline.proposal_id ? (
                  <>
                    <span className="mono pl-truncate" title={selectedPipeline.proposal_id}>{selectedPipeline.proposal_id}</span>
                    <CopyButton text={selectedPipeline.proposal_id} label="Proposal ID" />
                  </>
                ) : (
                  <span className="pl-muted">None</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Approval</dt>
              <dd className="pl-inline">
                {selectedPipeline.approval_id ? (
                  <>
                    <span className="mono pl-truncate" title={selectedPipeline.approval_id}>{selectedPipeline.approval_id}</span>
                    <CopyButton text={selectedPipeline.approval_id} label="Approval ID" />
                  </>
                ) : (
                  <span className="pl-muted">Unassigned</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Mutation</dt>
              <dd className="pl-inline">
                {selectedPipeline.mutation_id ? (
                  <>
                    <span className="mono pl-truncate" title={selectedPipeline.mutation_id}>{selectedPipeline.mutation_id}</span>
                    <CopyButton text={selectedPipeline.mutation_id} label="Mutation ID" />
                  </>
                ) : (
                  <span className="pl-muted">None</span>
                )}
              </dd>
            </div>
          </dl>
        </section>
      )}

      {/* Ledger */}
      <section className="pl-ledger" aria-labelledby="pl-ledger-title">
        <div className="pl-ledger-head">
          <h2 id="pl-ledger-title">Pipeline Audit Ledger</h2>
          <span className="pl-muted">Select a row to inspect its stages</span>
        </div>

        {error ? (
          <div className="pl-state pl-state-error" role="alert">
            <AlertTriangle size={18} />
            <div className="pl-state-title">Failed to load pipelines</div>
            <div className="pl-state-desc">{error}</div>
            <button type="button" className="btn btn-sm" onClick={loadPipelines}>
              Retry
            </button>
          </div>
        ) : isLoading ? (
          <TableSkeleton label="Loading pipeline execution logs" columns={[2, 3, 2, 2, 1, 1, 1, 2, 2]} rows={10} />
        ) : filteredPipelines.length === 0 ? (
          <div className="pl-state">
            <p className="pl-state-title">No pipeline records match filter</p>
            <p className="pl-state-desc">Try clearing the search query.</p>
          </div>
        ) : (
          <div className="pl-table-wrap">
            <table className="pl-table" aria-label="Pipeline Executions Table">
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col">Pipeline</th>
                  <th scope="col">Repository</th>
                  <th scope="col">Incident</th>
                  <th scope="col">Run</th>
                  <th scope="col">SHA</th>
                  <th scope="col">PR</th>
                  <th scope="col">Failure reason</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {filteredPipelines.map((pipe) => {
                  const isSelected = selectedPipeline?.pipeline_id === pipe.pipeline_id
                  return (
                    <tr
                      key={pipe.pipeline_id}
                      onClick={() => setSelectedPipeline(pipe)}
                      className={isSelected ? 'is-selected' : ''}
                      aria-selected={isSelected}
                    >
                      <td>
                        <StatusBadge status={pipe.status} type="pipeline" />
                      </td>

                      <td>
                        <div className="pl-cell-id">
                          <span className="mono pl-id pl-truncate" title={pipe.pipeline_id}>{pipe.pipeline_id}</span>
                          <CopyButton text={pipe.pipeline_id} label="Pipeline ID" />
                        </div>
                      </td>

                      <td>
                        <span className="pl-repo">
                          {pipe.repository_owner}/{pipe.repository_name}
                        </span>
                      </td>

                      <td>
                        <Link
                          to={`/incidents/${pipe.incident_id}`}
                          className="mono pl-incident"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {pipe.incident_id.slice(0, 12)}
                        </Link>
                      </td>

                      <td className="mono pl-muted">#{pipe.run_id}</td>

                      <td>
                        <div className="pl-cell-id">
                          <span className="mono pl-sha">{pipe.commit_sha.slice(0, 7)}</span>
                          <CopyButton text={pipe.commit_sha} label="SHA" />
                        </div>
                      </td>

                      <td>
                        {pipe.pr_url ? (
                          <a
                            href={pipe.pr_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="pl-pr"
                          >
                            <span className="mono">#{pipe.pr_number}</span>
                            <ExternalLink size={10} />
                          </a>
                        ) : (
                          <span className="pl-muted">—</span>
                        )}
                      </td>

                      <td>
                        {pipe.failure_reason ? (
                          <div className="pl-failure" title={pipe.failure_reason}>
                            {pipe.failure_reason}
                          </div>
                        ) : (
                          <span className="pl-muted">—</span>
                        )}
                      </td>

                      <td className="pl-muted pl-nowrap">
                        {new Date(pipe.updated_at).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="pl-pager">
          <span className="pl-muted">
            Page {page + 1} · offset {page * pageSize} · limit {pageSize}
          </span>
          <div className="pl-pager-buttons">
            <button
              type="button"
              className="btn btn-sm"
              disabled={page === 0 || isLoading}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              aria-label="Previous page"
            >
              <ChevronLeft size={13} />
              <span>Previous</span>
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={pipelines.length < pageSize || isLoading}
              onClick={() => setPage((p) => p + 1)}
              aria-label="Next page"
            >
              <span>Next</span>
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}

// Inline arrow keeps the detail header action visually consistent with other row links
const ArrowIcon: React.FC = () => <ChevronRight size={12} aria-hidden="true" />
