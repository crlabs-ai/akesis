import React, { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient, ApiError } from '../../services/api'
import type { IncidentSummary } from '../../types/api'
import { StatusBadge } from '../Common/StatusBadge'
import { TableSkeleton } from '../Common/Skeleton'
import { CopyButton } from '../Common/CopyButton'
import { AlertTriangle, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, Search } from 'lucide-react'

export const IncidentsList: React.FC = () => {
  const navigate = useNavigate()
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState<number>(0)
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState<string>('')
  const pageSize = 20

  const loadIncidents = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await apiClient.getIncidents({ limit: pageSize, offset: page * pageSize })
      setIncidents(data)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Unable to load incidents from backend API')
      }
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadIncidents()
  }, [page])

  // Filter in-memory for loaded page
  const filteredIncidents = incidents.filter((inc) => {
    if (categoryFilter !== 'all' && inc.category !== categoryFilter) {
      return false
    }
    if (searchQuery.trim() !== '') {
      const q = searchQuery.toLowerCase()
      const matchesId = inc.incident_id.toLowerCase().includes(q)
      const matchesRepo = inc.repository.toLowerCase().includes(q)
      const matchesSha = inc.commit_sha.toLowerCase().includes(q)
      const matchesWorkflow = inc.workflow_name.toLowerCase().includes(q)
      const matchesError = inc.error_type.toLowerCase().includes(q)
      if (!matchesId && !matchesRepo && !matchesSha && !matchesWorkflow && !matchesError) {
        return false
      }
    }
    return true
  })

  // Extract available categories from current records
  const uniqueCategories = Array.from(new Set(incidents.map((i) => i.category))).filter(Boolean)

  return (
    <div className="in-view">
      {/* Page head: title and loaded-record summary */}
      <header className="in-head">
        <div className="in-title">
          <h1>Incidents</h1>
          <span className="in-sub">CI failure events ingested from GitHub Actions</span>
        </div>
        <div className="in-head-end">
          <span className="in-count">
            {isLoading ? '—' : `${filteredIncidents.length} of ${incidents.length} loaded`} · page {page + 1}
          </span>
          <button
            type="button"
            className="btn btn-sm btn-icon"
            onClick={loadIncidents}
            disabled={isLoading}
            title="Refresh list"
            aria-label="Refresh list"
          >
            <RefreshCw size={13} className={isLoading ? 'spinner' : ''} />
          </button>
        </div>
      </header>

      {/* Toolbar: search and category filter */}
      <div className="in-toolbar">
        <div className="in-search">
          <Search size={13} aria-hidden="true" />
          <input
            type="text"
            placeholder="Filter by ID, repository, SHA, workflow or error"
            className="input-text in-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search incidents"
          />
        </div>

        <label className="in-filter">
          <span className="in-filter-label">Category</span>
          <select
            className="select-input"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            aria-label="Filter by category"
          >
            <option value="all">All categories</option>
            {uniqueCategories.map((cat) => (
              <option key={cat} value={cat}>
                {cat.replace('_', ' ')}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Ledger */}
      <section className="in-ledger" aria-labelledby="in-ledger-title">
        <div className="in-ledger-head">
          <h2 id="in-ledger-title">Incident queue</h2>
          <span className="in-muted">The list never includes raw diff content. Open an incident for its patch.</span>
        </div>

        {error ? (
          <div className="in-state in-state-error" role="alert">
            <AlertTriangle size={18} />
            <div className="in-state-title">Failed to load incidents</div>
            <div className="in-state-desc">{error}</div>
            <button type="button" className="btn btn-sm" onClick={loadIncidents}>
              Retry
            </button>
          </div>
        ) : isLoading ? (
          <TableSkeleton label="Loading incident records" columns={[3, 2, 2, 1, 1, 1, 1, 1, 1]} rows={10} />
        ) : filteredIncidents.length === 0 ? (
          <div className="in-state">
            <p className="in-state-title">No incidents match current filter</p>
            <p className="in-state-desc">Try clearing the search query or selecting all categories.</p>
          </div>
        ) : (
          <div className="in-table-wrap">
            <table className="in-table" aria-label="Incidents List">
              <thead>
                <tr>
                  <th scope="col">Incident</th>
                  <th scope="col">Repository</th>
                  <th scope="col">Workflow · branch</th>
                  <th scope="col">Category</th>
                  <th scope="col">Stage</th>
                  <th scope="col">Approval</th>
                  <th scope="col">Commit</th>
                  <th scope="col">PR</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {filteredIncidents.map((inc) => (
                  <tr
                    key={inc.incident_id}
                    onClick={() => navigate(`/incidents/${inc.incident_id}`)}
                    className="in-row"
                  >
                    <td>
                      <div className="in-cell-id">
                        <Link
                          to={`/incidents/${inc.incident_id}`}
                          className="mono in-id"
                          title={inc.incident_id}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {inc.incident_id.slice(0, 14)}
                        </Link>
                        <span onClick={(e) => e.stopPropagation()}>
                          <CopyButton text={inc.incident_id} label="Incident ID" />
                        </span>
                      </div>
                      <div className="in-error mono" title={inc.error_type}>
                        {inc.error_type}
                      </div>
                    </td>

                    <td>
                      <span className="in-repo" title={inc.repository}>{inc.repository}</span>
                    </td>

                    <td>
                      <div className="in-workflow" title={inc.workflow_name}>{inc.workflow_name}</div>
                      <div className="in-branch mono" title={inc.branch}>{inc.branch}</div>
                    </td>

                    <td>
                      <StatusBadge status={inc.category} type="category" />
                    </td>

                    <td>
                      <StatusBadge status={inc.current_stage} type="stage" />
                    </td>

                    <td>
                      {inc.approval_status ? (
                        <StatusBadge status={inc.approval_status} type="approval" />
                      ) : (
                        <span className="in-muted">—</span>
                      )}
                    </td>

                    <td>
                      <div className="in-cell-id">
                        <span className="mono in-sha">{inc.commit_sha.slice(0, 7)}</span>
                        <span onClick={(e) => e.stopPropagation()}>
                          <CopyButton text={inc.commit_sha} label="Commit SHA" />
                        </span>
                      </div>
                    </td>

                    <td>
                      {inc.pr_url ? (
                        <a
                          href={inc.pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="in-pr"
                        >
                          <span className="mono">#{inc.pr_number}</span>
                          <ExternalLink size={10} />
                        </a>
                      ) : (
                        <span className="in-muted">—</span>
                      )}
                    </td>

                    <td className="in-muted in-nowrap">
                      {new Date(inc.created_at).toLocaleString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="in-pager">
          <span className="in-muted">
            Page {page + 1} · offset {page * pageSize} · limit {pageSize}
          </span>
          <div className="in-pager-buttons">
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
              disabled={incidents.length < pageSize || isLoading}
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
