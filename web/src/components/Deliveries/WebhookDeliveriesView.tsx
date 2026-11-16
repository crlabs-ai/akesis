import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Copy, Info, RefreshCw, Search, X } from 'lucide-react'
import { ApiError, apiClient } from '../../services/api'
import { useTokenChangeReload } from '../../hooks/useTokenChangeReload'
import type { WebhookDelivery, WebhookDeliveryPage } from '../../types/api'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { Drawer } from '../Common/Drawer'
import { describeNextRetry, formatTime } from '../../lib/deliveryFormat'
import { describeFailureCode } from '../../lib/failureText'
import { formatExact, formatRelative } from '../../lib/time'

const PAGE_SIZE = 25

interface FilterOption {
  id: 'all' | 'retryable' | 'exhausted'
  label: string
  statuses?: string[]
}

const FILTERS: FilterOption[] = [
  { id: 'all', label: 'All operator states' },
  { id: 'retryable', label: 'Retryable', statuses: ['retryable_error'] },
  { id: 'exhausted', label: 'Exhausted', statuses: ['exhausted'] },
]

const STATUS_LABEL: Record<string, string> = {
  retryable_error: 'Retryable',
  exhausted: 'Exhausted',
  provider_hold: 'Provider hold',
}

/** Names the reason a refresh failed, so the operator knows whether to fix the token or the network. */
const describeFailure = (error: unknown): string => {
  if (!(error instanceof ApiError)) return 'The latest request failed.'
  switch (error.kind) {
    case 'unauthorized':
      return 'The read token was rejected.'
    case 'forbidden':
      return 'This token may not view deliveries.'
    case 'network':
      return 'The backend is unreachable.'
    case 'timeout':
      return 'The backend did not respond in time.'
    case 'server':
      return 'The backend reported an error.'
    default:
      return 'The latest request failed.'
  }
}

const StatusChip: React.FC<{ status: string }> = ({ status }) => (
  <span className={`delivery-status delivery-status-${status}`}>{STATUS_LABEL[status] ?? status}</span>
)

/** Attempts used against the budget, drawn as a quiet bar. The text carries the same information. */
const AttemptMeter: React.FC<{ used: number; max: number; status: string }> = ({ used, max, status }) => {
  const ratio = max > 0 ? Math.min(1, used / max) : 0
  return (
    <div className="attempt-meter" aria-label={`${used} of ${max} attempts used`}>
      <span className="mono attempt-text">
        {used} / {max}
      </span>
      <span className="attempt-track" aria-hidden="true">
        <span className={`attempt-fill attempt-fill-${status}`} style={{ width: `${ratio * 100}%` }} />
      </span>
    </div>
  )
}

const CopyText: React.FC<{ value: string; label: string }> = ({ value, label }) => {
  const [copied, setCopied] = useState<boolean>(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      setCopied(false)
    }
  }
  return (
    <button
      type="button"
      className="btn btn-sm btn-icon copy-btn"
      onClick={(event) => {
        event.stopPropagation()
        void copy()
      }}
      aria-label={`Copy ${label}`}
      title={copied ? 'Copied' : `Copy ${label}`}
    >
      <Copy size={12} aria-hidden="true" />
      <span className="sr-only" role="status">
        {copied ? 'Copied' : ''}
      </span>
    </button>
  )
}

export const WebhookDeliveriesView: React.FC = () => {
  const [filterId, setFilterId] = useState<FilterOption['id']>('all')
  const [query, setQuery] = useState<string>('')
  // cursors[i] is the cursor that fetches page i. cursors[0] is null (the first page).
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [pageIndex, setPageIndex] = useState<number>(0) // page requested
  const [loadedIndex, setLoadedIndex] = useState<number>(0) // page whose rows are on screen
  const [data, setData] = useState<WebhookDeliveryPage | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<unknown>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [clock, setClock] = useState<number>(0)
  const [selected, setSelected] = useState<WebhookDelivery | null>(null)
  const requestSeq = useRef<number>(0)

  const filter = FILTERS.find((item) => item.id === filterId) ?? FILTERS[0]

  const load = useCallback(async (cursor: string | null, statuses: string[] | undefined, index: number) => {
    const seq = ++requestSeq.current
    setIsLoading(true)
    try {
      const page = await apiClient.getWebhookDeliveries({
        status: statuses,
        limit: PAGE_SIZE,
        cursor: cursor ?? undefined,
      })
      if (seq !== requestSeq.current) return // a newer request superseded this one
      setData(page)
      setLoadedIndex(index)
      setClock(Date.now())
      setError(null)
      setLastUpdated(new Date())
    } catch (err) {
      if (seq !== requestSeq.current) return
      // Keep any rows already shown, but record the failure. Never convert it to an empty list.
      setError(err)
    } finally {
      if (seq === requestSeq.current) setIsLoading(false)
    }
  }, [])

  // Changing the filter resets the page cursors in the same update, so no request uses a stale cursor.
  const changeFilter = (next: FilterOption['id']) => {
    setFilterId(next)
    setCursors([null])
    setPageIndex(0)
    setLoadedIndex(0)
  }

  const reloadRequested = useCallback(
    () => void load(cursors[pageIndex] ?? null, filter.statuses, pageIndex),
    [cursors, filter.statuses, load, pageIndex]
  )

  useEffect(() => {
    reloadRequested()
  }, [reloadRequested])

  useTokenChangeReload(reloadRequested)

  // Navigation is relative to the page on screen, not the page last requested, so a failed
  // navigation cannot make the next click skip a page.
  const goOlder = () => {
    if (!data?.next_cursor || isLoading) return
    setCursors((prev) => [...prev.slice(0, loadedIndex + 1), data.next_cursor])
    setPageIndex(loadedIndex + 1)
  }

  const goNewer = () => {
    if (loadedIndex === 0 || isLoading) return
    setPageIndex(loadedIndex - 1)
  }

  const items = useMemo(() => data?.items ?? [], [data])
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle === '' ? items : items.filter((row) => row.delivery_id.toLowerCase().includes(needle))
  }, [items, query])

  // Counts are for the rows on this page only. They are never presented as totals.
  const pageCounts = useMemo(() => {
    const count = (status: string) => items.filter((row) => row.status === status).length
    return {
      retryable: count('retryable_error'),
      exhausted: count('exhausted'),
      hold: count('provider_hold'),
    }
  }, [items])

  const showingStale = data !== null && error !== null
  const filtersActive = filterId !== 'all' || query.trim() !== ''

  return (
    <div className="deliveries-view">
      <header className="deliveries-header">
        <div className="deliveries-title">
          <h1>Webhook deliveries</h1>
          <p className="deliveries-sub">
            Deliveries that are scheduled for retry or have used every attempt. The retry worker runs scheduled
            attempts. This page is read-only. There is no requeue action: manual requeue is not implemented.
          </p>
        </div>
        <div className="deliveries-actions">
          <label className="deliveries-search">
            <Search size={13} aria-hidden="true" />
            <span className="sr-only">Filter this page by delivery ID</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter this page by delivery ID"
              spellCheck={false}
            />
            {query !== '' && (
              <button type="button" className="btn btn-sm btn-icon" onClick={() => setQuery('')} aria-label="Clear search">
                <X size={12} aria-hidden="true" />
              </button>
            )}
          </label>
          <button type="button" className="btn btn-sm" onClick={reloadRequested} disabled={isLoading}>
            <RefreshCw size={13} className={isLoading ? 'spinner' : ''} aria-hidden="true" />
            <span>{isLoading ? 'Loading…' : 'Refresh'}</span>
          </button>
        </div>
      </header>

      <div className="deliveries-summary" aria-label="Counts for the rows on this page">
        <div className="summary-tile">
          <span className="summary-label">Shown on this page</span>
          <span className="summary-value mono">{isLoading && data === null ? '—' : items.length}</span>
        </div>
        <div className="summary-tile tone-info">
          <span className="summary-label">Retryable</span>
          <span className="summary-value mono">{pageCounts.retryable}</span>
        </div>
        <div className="summary-tile tone-danger">
          <span className="summary-label">Exhausted</span>
          <span className="summary-value mono">{pageCounts.exhausted}</span>
        </div>
        <div className="summary-tile tone-warn">
          <span className="summary-label">Provider hold</span>
          <span className="summary-value mono">{pageCounts.hold}</span>
        </div>
        <p className="summary-note">
          <Info size={12} aria-hidden="true" /> These counts describe this page only. The backend does not provide
          totals across all deliveries.
        </p>
      </div>

      <div className="deliveries-toolbar">
        <div className="deliveries-filters" role="group" aria-label="Filter deliveries by status">
          {FILTERS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`btn btn-sm${filterId === option.id ? ' is-active' : ''}`}
              aria-pressed={filterId === option.id}
              onClick={() => changeFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
          {filtersActive && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setQuery('')
                changeFilter('all')
              }}
            >
              Reset filters
            </button>
          )}
        </div>
        <div className="deliveries-meta mono">
          {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Not loaded yet'} · page {loadedIndex + 1}
        </div>
      </div>

      {showingStale && (
        <div className="deliveries-stale" role="alert">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>
            Showing page {loadedIndex + 1}, loaded {lastUpdated?.toLocaleTimeString() ?? 'earlier'}. {describeFailure(error)}
            These rows may be out of date.
          </span>
          <button type="button" className="btn btn-sm" onClick={reloadRequested} disabled={isLoading}>
            Retry
          </button>
        </div>
      )}

      {error !== null && data === null ? (
        <ApiErrorPanel error={error} subject="webhook deliveries" onRetry={reloadRequested} />
      ) : (
        <div className={`deliveries-table-wrap${isLoading ? ' is-loading' : ''}`} aria-busy={isLoading}>
          {isLoading && data === null ? (
            <div className="deliveries-skeleton" role="status" aria-label="Loading deliveries">
              {Array.from({ length: 6 }, (_, index) => (
                <div key={index} className="skeleton-row">
                  <span className="skeleton-cell w-40" />
                  <span className="skeleton-cell w-16" />
                  <span className="skeleton-cell w-24" />
                  <span className="skeleton-cell w-32" />
                  <span className="skeleton-cell w-24" />
                </div>
              ))}
            </div>
          ) : items.length === 0 ? (
            <p className="deliveries-empty" role="status">
              No deliveries match this filter. The backend answered successfully with zero rows.
            </p>
          ) : visible.length === 0 ? (
            <p className="deliveries-empty" role="status">
              No delivery on this page matches “{query}”. Clear the search, or open the next page.
            </p>
          ) : (
            <table className="deliveries-table">
              <thead>
                <tr>
                  <th scope="col">Delivery</th>
                  <th scope="col">Status</th>
                  <th scope="col">Attempts</th>
                  <th scope="col">Failure</th>
                  <th scope="col">Next retry</th>
                  <th scope="col">Received</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((delivery) => {
                  const meaning = describeFailureCode(delivery.failure_code)
                  const due = formatRelative(delivery.next_retry_at, clock)
                  return (
                    <tr key={delivery.delivery_id} className="delivery-row" onClick={() => setSelected(delivery)}>
                      <td>
                        {/* The ID is the keyboard target. The row click is a mouse convenience only. */}
                        <button
                          type="button"
                          className="mono delivery-id delivery-open"
                          title={delivery.delivery_id}
                          aria-label={`Open details for delivery ${delivery.delivery_id}`}
                          onClick={(event) => {
                            event.stopPropagation()
                            setSelected(delivery)
                          }}
                        >
                          {delivery.delivery_id}
                        </button>
                      </td>
                      <td>
                        <StatusChip status={delivery.status} />
                      </td>
                      <td>
                        <AttemptMeter used={delivery.attempt_count} max={delivery.max_attempts} status={delivery.status} />
                      </td>
                      <td>
                        <div className="failure-cell">
                          <span className="failure-human">{meaning ?? delivery.failure_code ?? '—'}</span>
                          {delivery.failure_code && <span className="mono failure-code">{delivery.failure_code}</span>}
                        </div>
                      </td>
                      <td title={formatExact(delivery.next_retry_at)}>
                        {delivery.status === 'exhausted' ? (
                          <span className="muted">No further attempts</span>
                        ) : !delivery.next_retry_at ? (
                          <span className="muted">Not scheduled</span>
                        ) : (
                          <span>{due}</span>
                        )}
                      </td>
                      <td title={formatExact(delivery.received_at)}>
                        {formatRelative(delivery.received_at, clock) ?? formatTime(delivery.received_at)}
                      </td>
                      <td>
                        <CopyText value={delivery.delivery_id} label="delivery ID" />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      <nav className="deliveries-pager" aria-label="Delivery pages">
        <button type="button" className="btn btn-sm" onClick={goNewer} disabled={loadedIndex === 0 || isLoading}>
          <ChevronLeft size={13} aria-hidden="true" /> Newer
        </button>
        <button type="button" className="btn btn-sm" onClick={goOlder} disabled={!data?.next_cursor || isLoading}>
          Older <ChevronRight size={13} aria-hidden="true" />
        </button>
      </nav>

      <Drawer open={selected !== null} title="Delivery details" onClose={() => setSelected(null)}>
        {selected && <DeliveryDetail delivery={selected} now={clock} />}
      </Drawer>
    </div>
  )
}

const DeliveryDetail: React.FC<{ delivery: WebhookDelivery; now: number }> = ({ delivery, now }) => {
  const meaning = describeFailureCode(delivery.failure_code)
  return (
    <dl className="detail-list">
      <div className="detail-row">
        <dt>Delivery ID</dt>
        <dd>
          <span className="detail-id-row">
            <span className="mono detail-id">{delivery.delivery_id}</span>
            <CopyText value={delivery.delivery_id} label="delivery ID" />
          </span>
        </dd>
      </div>
      <div className="detail-row">
        <dt>Status</dt>
        <dd>
          <StatusChip status={delivery.status} />
        </dd>
      </div>
      <div className="detail-row">
        <dt>Attempts</dt>
        <dd>
          <AttemptMeter used={delivery.attempt_count} max={delivery.max_attempts} status={delivery.status} />
        </dd>
      </div>
      <div className="detail-row">
        <dt>Failure</dt>
        <dd>
          <div>{meaning ?? 'No description for this code'}</div>
          {delivery.failure_code && <div className="mono muted">{delivery.failure_code}</div>}
        </dd>
      </div>
      <div className="detail-row">
        <dt>Last error</dt>
        <dd className="mono">{delivery.last_error_code ?? '—'}</dd>
      </div>
      <div className="detail-row">
        <dt>Next retry</dt>
        <dd>
          {describeNextRetry(delivery, now)}
          {delivery.next_retry_at && <div className="mono muted">{formatExact(delivery.next_retry_at)}</div>}
        </dd>
      </div>
      <div className="detail-row">
        <dt>Event</dt>
        <dd className="mono">{delivery.event_type}</dd>
      </div>
      <div className="detail-row">
        <dt>Received</dt>
        <dd className="mono">{formatExact(delivery.received_at)}</dd>
      </div>
      <div className="detail-row">
        <dt>Updated</dt>
        <dd className="mono">{formatExact(delivery.updated_at)}</dd>
      </div>
      <p className="detail-note">
        Payloads, signatures and credentials are never shown here. This page cannot requeue a delivery. To retry an
        exhausted delivery, re-run the workflow in GitHub; a re-run creates a new delivery.
      </p>
    </dl>
  )
}
