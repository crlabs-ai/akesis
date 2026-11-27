import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebhookDeliveriesView } from '../components/Deliveries/WebhookDeliveriesView'
import { describeNextRetry } from '../lib/deliveryFormat'
import { ApiErrorPanel } from '../components/Common/ApiErrorPanel'
import { ApiError, apiClient } from '../services/api'
import { session } from '../services/session'
import type { WebhookDelivery, WebhookDeliveryPage } from '../types/api'

const delivery = (overrides: Partial<WebhookDelivery> = {}): WebhookDelivery => ({
  delivery_id: 'gh-delivery-0001',
  status: 'retryable_error',
  failure_code: 'github_rate_limited',
  attempt_count: 2,
  max_attempts: 5,
  next_retry_at: new Date(Date.now() + 10 * 60_000).toISOString(),
  last_error_code: 'github_rate_limited',
  received_at: '2026-11-16T03:00:00Z',
  updated_at: '2026-11-16T03:05:00Z',
  event_type: 'workflow_run',
  ...overrides,
})

const page = (items: WebhookDelivery[], next: string | null = null): WebhookDeliveryPage => ({
  items,
  next_cursor: next,
})

describe('WebhookDeliveriesView', () => {
  beforeEach(() => {
    session.reset()
    vi.restoreAllMocks()
  })
  afterEach(() => session.reset())

  const renderView = () =>
    render(
      <MemoryRouter>
        <WebhookDeliveriesView />
      </MemoryRouter>
    )

  it('renders the backend rows with attempt counts and the retry state', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue(
      page([
        delivery(),
        delivery({ delivery_id: 'gh-delivery-0002', status: 'exhausted', next_retry_at: null, attempt_count: 5 }),
        delivery({ delivery_id: 'gh-delivery-0003', status: 'provider_hold' }),
      ])
    )
    renderView()
    await waitFor(() => expect(screen.getByText('gh-delivery-0001')).toBeInTheDocument())
    // Two rows share 2 / 5 (retryable and provider hold); the exhausted row shows 5 / 5.
    expect(screen.getAllByText('2 / 5')).toHaveLength(2)
    expect(screen.getByText('5 / 5')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Exhausted')).toBeInTheDocument()
    expect(within(table).getByText('Provider hold')).toBeInTheDocument()
    expect(screen.getAllByText('github_rate_limited').length).toBeGreaterThan(0)
  })

  it('offers no requeue or mutation control', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue(page([delivery()]))
    renderView()
    await waitFor(() => expect(screen.getByText('gh-delivery-0001')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /requeue|retry now|re-?run|replay/i })).not.toBeInTheDocument()
    expect(screen.getByText(/There is no requeue action/)).toBeInTheDocument()
  })

  it('requests only the status the chosen filter names', async () => {
    const spy = vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue(page([]))
    renderView()
    await waitFor(() => expect(spy).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Exhausted' }))
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ status: ['exhausted'], limit: 25, cursor: undefined }))
    fireEvent.click(screen.getByRole('button', { name: 'Retryable' }))
    await waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith({ status: ['retryable_error'], limit: 25, cursor: undefined })
    )
  })

  it('pages forward with the opaque cursor and back to the newest page', async () => {
    const spy = vi
      .spyOn(apiClient, 'getWebhookDeliveries')
      .mockResolvedValueOnce(page([delivery({ delivery_id: 'page-one' })], 'cursor-A'))
      .mockResolvedValueOnce(page([delivery({ delivery_id: 'page-two' })], null))
      // Returning to the newest page re-requests it: the view does not cache pages.
      .mockResolvedValueOnce(page([delivery({ delivery_id: 'page-one' })], 'cursor-A'))
    renderView()
    await waitFor(() => expect(screen.getByText('page-one')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Older/ }))
    await waitFor(() => expect(screen.getByText('page-two')).toBeInTheDocument())
    expect(spy).toHaveBeenLastCalledWith({ status: undefined, limit: 25, cursor: 'cursor-A' })
    fireEvent.click(screen.getByRole('button', { name: /Newer/ }))
    await waitFor(() => expect(screen.getByText('page-one')).toBeInTheDocument())
  })

  it('shows an empty success only when the backend returned zero rows', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue(page([]))
    renderView()
    await waitFor(() =>
      expect(screen.getByText(/zero rows/)).toBeInTheDocument()
    )
  })

  it('reports an ended session and offers sign-in instead of an empty table', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockRejectedValue(
      new ApiError('not_signed_in', 401, undefined, 'unauthorized')
    )
    renderView()
    await waitFor(() => expect(screen.getByText('Sign in required')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Sign in with GitHub' })).toHaveAttribute('href', '/login')
    expect(screen.queryByText(/zero rows/)).not.toBeInTheDocument()
  })

  it('names an ended session in the stale banner while keeping the rows that loaded', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries')
      .mockResolvedValueOnce(page([delivery({ delivery_id: 'kept-under-401' })]))
      .mockRejectedValueOnce(new ApiError('not_signed_in', 401, undefined, 'unauthorized'))
    renderView()
    await waitFor(() => expect(screen.getByText('kept-under-401')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Exhausted' }))
    await waitFor(() => expect(screen.getByText(/Your session ended/)).toBeInTheDocument())
    expect(screen.getByText('kept-under-401')).toBeInTheDocument()
  })

  it('keeps rows visible and flags them stale when a later refresh fails', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries')
      .mockResolvedValueOnce(page([delivery({ delivery_id: 'kept-row' })]))
      .mockRejectedValueOnce(new ApiError('The backend is unreachable.', 0, undefined, 'network'))
    renderView()
    await waitFor(() => expect(screen.getByText('kept-row')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Exhausted' }))
    await waitFor(() => expect(screen.getByText(/may be out of date/)).toBeInTheDocument())
    expect(screen.getByText('kept-row')).toBeInTheDocument()
  })
})

describe('describeNextRetry', () => {
  const now = Date.parse('2026-11-16T12:00:00Z')

  it('never claims that a past due time is running now', () => {
    const text = describeNextRetry(delivery({ next_retry_at: '2026-11-16T11:59:00Z' }), now)
    expect(text).toMatch(/Due\. The retry worker claims it on its next pass\./)
  })

  it('says exhausted deliveries have no further attempts', () => {
    expect(describeNextRetry(delivery({ status: 'exhausted', next_retry_at: null }), now)).toBe(
      'No further attempts'
    )
  })
})

describe('ApiErrorPanel', () => {
  it('offers GitHub sign-in for an unauthorized failure', () => {
    render(
      <MemoryRouter>
        <ApiErrorPanel error={new ApiError('nope', 401, undefined, 'unauthorized')} />
      </MemoryRouter>
    )
    expect(screen.getByText('Sign in required')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in with GitHub' })).toBeInTheDocument()
  })

  it('never describes a server failure as missing data', () => {
    render(<ApiErrorPanel error={new ApiError('boom', 500, undefined, 'server')} subject="pipelines" />)
    expect(screen.getByText(/unavailable, not empty/)).toBeInTheDocument()
  })
})
