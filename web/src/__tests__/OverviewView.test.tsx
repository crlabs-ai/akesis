import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { OverviewView } from '../components/Overview/OverviewView'
import { ApiError, apiClient } from '../services/api'
import {
  mockIncidentSummaryList,
  mockPipelineSummaryList,
  mockReadinessResponse,
} from './fixtures'

describe('OverviewView (Command Center)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders live status and accurately derived counts without fabricated metrics', async () => {
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue(mockIncidentSummaryList)
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)

    render(
      <MemoryRouter>
        <OverviewView />
      </MemoryRouter>
    )

    // Wait for loaded state
    await waitFor(() => {
      expect(screen.getByText('REPORTS READY')).toBeInTheDocument()
    })

    // The probe is a process check; the page must say so rather than imply a database check.
    expect(screen.getByText('process only · database not checked')).toBeInTheDocument()
    // Counts are page sizes, labelled as such.
    expect(screen.getByText('Incidents shown (newest 8):')).toBeInTheDocument()
    expect(screen.getByText('Pipelines shown (newest 8):')).toBeInTheDocument()
    // A count over one page is not a total, so the derived 'Pending Approvals' figure is gone.
    expect(screen.queryByText('Pending Approvals:')).not.toBeInTheDocument()

    // Verify recent incident table renders real data
    expect(screen.getByText('crlabs/backend-service')).toBeInTheDocument()
    expect(screen.getByText('crlabs/data-pipeline')).toBeInTheDocument()

    // Verify no fabricated uptime, MTTR or fake 99.99% claims exist
    expect(screen.queryByText(/99\.99%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/MTTR/)).not.toBeInTheDocument()
  })

  it('handles backend readiness failure gracefully', async () => {
    vi.spyOn(apiClient, 'getReadiness').mockRejectedValue(new Error('Connection refused'))
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue([])
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue([])

    render(
      <MemoryRouter>
        <OverviewView />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('UNREACHABLE')).toBeInTheDocument()
    })
  })

  it('shows a failed incidents request as an error, never as an empty list', async () => {
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockRejectedValue(
      new ApiError('The backend is unreachable.', 0, undefined, 'network')
    )
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)

    render(
      <MemoryRouter>
        <OverviewView />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Backend unreachable')).toBeInTheDocument()
    })
    expect(screen.queryByText('The backend returned no incidents.')).not.toBeInTheDocument()
    // The other source still renders on its own.
    expect(screen.getByText('pipe_test_01')).toBeInTheDocument()
  })
})
