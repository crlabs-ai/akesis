import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { App } from '../App'
import { apiClient } from '../services/api'
import {
  mockIncidentSummaryList,
  mockPipelineSummaryList,
  mockReadinessResponse,
} from './fixtures'

describe('App Shell & Navigation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue(mockIncidentSummaryList)
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)
  })

  it('renders application shell, brand identity, and navigation destinations', async () => {
    render(<App />)

    // Brand and header
    expect(screen.getByText('AKESIS')).toBeInTheDocument()
    expect(screen.getByText('CRLabs')).toBeInTheDocument()

    // Navigation links
    expect(screen.getByRole('link', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Incidents' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Pipelines' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'System Health' })).toBeInTheDocument()

    // Header refresh button
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()

    // Wait for overview content to load
    await waitFor(() => {
      expect(screen.getByText('READY')).toBeInTheDocument()
    })
  })
})
