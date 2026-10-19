import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { IncidentsList } from '../components/Incidents/IncidentsList'
import { apiClient, ApiError } from '../services/api'
import { mockIncidentSummaryList } from './fixtures'

describe('IncidentsList', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders table columns and rows without requiring diff_content', async () => {
    const getIncidentsSpy = vi
      .spyOn(apiClient, 'getIncidents')
      .mockResolvedValue(mockIncidentSummaryList)

    render(
      <MemoryRouter>
        <IncidentsList />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('crlabs/backend-service')).toBeInTheDocument()
    })

    // Verify API called with pagination params
    expect(getIncidentsSpy).toHaveBeenCalledWith({ limit: 20, offset: 0 })

    // Verify incident metadata fields render
    expect(screen.getByText('CI Pipeline')).toBeInTheDocument()
    expect(screen.getByText('RuffLinterError')).toBeInTheDocument()
    expect(screen.getByText('crlabs/data-pipeline')).toBeInTheDocument()
    expect(screen.getByText('ModuleNotFoundError')).toBeInTheDocument()

    // Verify copyable commit SHA is displayed
    expect(screen.getByText('a1b2c3d')).toBeInTheDocument()

    // Verify diff_content is NOT rendered in list view
    expect(screen.queryByText('Unified Diff Patch')).not.toBeInTheDocument()
  })

  it('renders error state when API fails with ApiError', async () => {
    vi.spyOn(apiClient, 'getIncidents').mockRejectedValue(
      new ApiError('Unauthorized: API read token required or invalid', 401)
    )

    render(
      <MemoryRouter>
        <IncidentsList />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Failed to load incidents')).toBeInTheDocument()
      expect(
        screen.getByText('Unauthorized: API read token required or invalid')
      ).toBeInTheDocument()
    })
  })

  it('renders empty state when no records return', async () => {
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue([])

    render(
      <MemoryRouter>
        <IncidentsList />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('No incidents match current filter')).toBeInTheDocument()
    })
  })
})
