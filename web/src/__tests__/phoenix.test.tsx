import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebhookDeliveriesView } from '../components/Deliveries/WebhookDeliveriesView'
import { OverviewView } from '../components/Overview/OverviewView'
import { describeFailureCode } from '../lib/failureText'
import { formatRelative } from '../lib/time'
import { ApiError, apiClient } from '../services/api'
import { session } from '../services/session'
import type { WebhookDelivery } from '../types/api'
import { mockPipelineSummaryList, mockReadinessResponse, mockIncidentSummaryList } from './fixtures'

const NOW = Date.parse('2026-11-16T12:00:00Z')

const delivery = (overrides: Partial<WebhookDelivery> = {}): WebhookDelivery => ({
  delivery_id: 'gh-delivery-phx-1',
  status: 'retryable_error',
  failure_code: 'github_rate_limited',
  attempt_count: 2,
  max_attempts: 5,
  next_retry_at: new Date(Date.now() + 30 * 60_000).toISOString(),
  last_error_code: 'github_rate_limited',
  received_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  updated_at: new Date(Date.now() - 60_000).toISOString(),
  event_type: 'workflow_run',
  ...overrides,
})

describe('formatRelative', () => {
  it('describes past and future times in plain words', () => {
    expect(formatRelative(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe('5 minutes ago')
    expect(formatRelative(new Date(NOW + 54 * 60_000).toISOString(), NOW)).toBe('in 54 minutes')
    expect(formatRelative(new Date(NOW + 26 * 3600_000).toISOString(), NOW)).toBe('tomorrow')
  })

  it('returns null for missing or invalid timestamps rather than inventing a value', () => {
    expect(formatRelative(null, NOW)).toBeNull()
    expect(formatRelative('not-a-date', NOW)).toBeNull()
  })
})

describe('describeFailureCode', () => {
  it('explains known codes and leaves unknown codes unexplained', () => {
    expect(describeFailureCode('github_rate_limited')).toBe('GitHub rate limit reached')
    expect(describeFailureCode('made_up_code')).toBeNull()
    expect(describeFailureCode(null)).toBeNull()
  })
})

describe('Webhook deliveries workspace', () => {
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

  it('states the page-scoped counts and shows the human failure meaning beside the code', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({
      items: [delivery(), delivery({ delivery_id: 'gh-delivery-phx-2', status: 'exhausted', next_retry_at: null })],
      next_cursor: null,
    })
    renderView()
    await waitFor(() => expect(screen.getByText('gh-delivery-phx-1')).toBeInTheDocument())
    expect(screen.getByText(/These counts describe this page only/)).toBeInTheDocument()
    expect(screen.getAllByText('GitHub rate limit reached').length).toBeGreaterThan(0)
    expect(screen.getByText('No further attempts')).toBeInTheDocument()
  })

  it('filters the loaded page by delivery ID and offers a reset', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({
      items: [delivery({ delivery_id: 'alpha-111' }), delivery({ delivery_id: 'beta-222' })],
      next_cursor: null,
    })
    renderView()
    await waitFor(() => expect(screen.getByText('alpha-111')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Filter this page by delivery ID'), { target: { value: 'beta' } })
    expect(screen.queryByText('alpha-111')).not.toBeInTheDocument()
    expect(screen.getByText('beta-222')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }))
    await waitFor(() => expect(screen.getByText('alpha-111')).toBeInTheDocument())
  })

  it('opens a detail drawer from a row, closes it on Escape, and copies the delivery ID', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({ items: [delivery()], next_cursor: null })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    renderView()
    await waitFor(() => expect(screen.getByText('gh-delivery-phx-1')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Open details for delivery gh-delivery-phx-1' }))
    const dialog = screen.getByRole('dialog', { name: 'Delivery details' })
    expect(within(dialog).getByText(/Payloads, signatures and credentials are never shown/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Copy delivery ID' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('gh-delivery-phx-1'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Delivery details' })).not.toBeInTheDocument()
  })

  it('never offers a requeue or retry-now action in the drawer', async () => {
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({ items: [delivery()], next_cursor: null })
    renderView()
    await waitFor(() => expect(screen.getByText('gh-delivery-phx-1')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Open details for delivery gh-delivery-phx-1' }))
    const dialog = screen.getByRole('dialog', { name: 'Delivery details' })
    expect(within(dialog).queryByRole('button', { name: /requeue|retry now|replay/i })).not.toBeInTheDocument()
  })
})

describe('Command center honesty', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('reports the worker as not reported and the database as not checked', async () => {
    vi.spyOn(apiClient, 'getLiveness').mockResolvedValue({ status: 'ok' })
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue(mockIncidentSummaryList)
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({ items: [], next_cursor: null })
    render(
      <MemoryRouter>
        <OverviewView />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('Responding')).toBeInTheDocument())
    expect(screen.getByText('Not reported: the API has no worker status')).toBeInTheDocument()
    expect(screen.getByText('Not checked by any endpoint')).toBeInTheDocument()
  })

  it('shows the API process as unreachable when liveness fails, without hiding other sources', async () => {
    vi.spyOn(apiClient, 'getLiveness').mockRejectedValue(new ApiError('down', 0, undefined, 'network'))
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue(mockIncidentSummaryList)
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)
    vi.spyOn(apiClient, 'getWebhookDeliveries').mockResolvedValue({ items: [], next_cursor: null })
    render(
      <MemoryRouter>
        <OverviewView />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('Unreachable')).toBeInTheDocument())
    expect(screen.getByText('crlabs/backend-service')).toBeInTheDocument()
    await act(async () => {})
  })
})
