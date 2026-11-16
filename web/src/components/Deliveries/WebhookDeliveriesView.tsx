import React, { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Info } from 'lucide-react'
import { ApiError, apiClient } from '../../services/api'
import { useTokenChangeReload } from '../../hooks/useTokenChangeReload'
import type { WebhookDeliveryPage } from '../../types/api'
import { ApiErrorPanel } from '../Common/ApiErrorPanel'
import { describeNextRetry, formatTime } from '../../lib/deliveryFormat'

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

export const WebhookDeliveriesView: React.FC = () => {
  const [filterId, setFilterId] = useState<FilterOption['id']>('all')
  // cursors[i] is the cursor that fetches page i. cursors[0] is null (the first page).
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [pageIndex, setPageIndex] = useState<number>(0) // page requested
  const [loadedIndex, setLoadedIndex] = useState<number>(0) // page whose rows are on screen
  const [data, setData] = useState<WebhookDeliveryPage | null>(null)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<unknown>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const requestSeq = useRef<number>(0)
  const [clock, setClock] = useState<number>(0)

  const filter = FILTERS.find((item) => item.id === filterId) ?? FILTERS[0]

  const load = useCallback(
    async (cursor: string | null, statuses: string[] | undefined, index: number) => {
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
    },
    []
  )

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

  const items = data?.items ?? []
  const now = clock
  const showingStale = data !== null && error !== null

  return (
    <div className="deliveries-view">
      <header className="deliveries-header">
        <div>
          <h1>Webhook deliveries</h1>
          <p className="deliveries-sub">
            Read-only view of deliveries that are scheduled for retry or have exhausted their attempts.
            Retries run inside the retry worker. This page cannot requeue or change a delivery.
          </p>
        </div>
      </header>

      <div className="deliveries-notice" role="note">
        <Info size={14} aria-hidden="true" />
        <span>
          There is no requeue action. Manual requeue is not implemented. To retry an exhausted delivery,
          re-run the workflow in GitHub. A re-run creates a new delivery.
        </span>
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
            <p className="deliveries-loading" role="status">Loading deliveries…</p>
          ) : items.length === 0 ? (
            <p className="deliveries-empty" role="status">
              No deliveries match this filter. The backend answered successfully with zero rows.
            </p>
          ) : (
            <table className="deliveries-table">
              <thead>
                <tr>
                  <th scope="col">Delivery ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Attempts</th>
                  <th scope="col">Failure code</th>
                  <th scope="col">Last error</th>
                  <th scope="col">Next retry</th>
                  <th scope="col">Event</th>
                  <th scope="col">Received</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((delivery) => (
                  <tr key={delivery.delivery_id}>
                    <td className="mono delivery-id" title={delivery.delivery_id}>
                      {delivery.delivery_id}
                    </td>
                    <td>
                      <StatusChip status={delivery.status} />
                    </td>
                    <td className="mono">
                      {delivery.attempt_count} / {delivery.max_attempts}
                    </td>
                    <td className="mono">{delivery.failure_code ?? '—'}</td>
                    <td className="mono">{delivery.last_error_code ?? '—'}</td>
                    <td>{describeNextRetry(delivery, now)}</td>
                    <td className="mono">{delivery.event_type}</td>
                    <td>{formatTime(delivery.received_at)}</td>
                    <td>{formatTime(delivery.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <nav className="deliveries-pager" aria-label="Delivery pages">
        <button type="button" className="btn btn-sm" onClick={goNewer} disabled={loadedIndex === 0 || isLoading}>
          <ChevronLeft size={13} aria-hidden="true" /> Newer
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={goOlder}
          disabled={!data?.next_cursor || isLoading}
        >
          Older <ChevronRight size={13} aria-hidden="true" />
        </button>
      </nav>
    </div>
  )
}
