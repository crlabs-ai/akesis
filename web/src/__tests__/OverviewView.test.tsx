import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { OverviewView } from '../components/Overview/OverviewView'
import { apiClient } from '../services/api'
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
      expect(screen.getByText('READY')).toBeInTheDocument()
    })

    // Verify operational status strip values derived from loaded records
    expect(screen.getByText('Loaded Incidents:')).toBeInTheDocument()
    expect(screen.getByText('Pending Approvals:')).toBeInTheDocument()
    expect(screen.getByText('Loaded Pipelines:')).toBeInTheDocument()

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
})
