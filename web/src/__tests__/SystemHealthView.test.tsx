import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SystemHealthView } from '../components/Health/SystemHealthView'
import { apiClient } from '../services/api'
import { mockLivenessResponse, mockReadinessResponse } from './fixtures'

describe('SystemHealthView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders liveness and readiness probes accurately', async () => {
    vi.spyOn(apiClient, 'getLiveness').mockResolvedValue(mockLivenessResponse)
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)

    render(<SystemHealthView />)

    await waitFor(() => {
      expect(screen.getByText('System Health & Diagnostics')).toBeInTheDocument()
    })

    // Liveness and readiness badges
    expect(screen.getByText('OK')).toBeInTheDocument()
    expect(screen.getByText('READY')).toBeInTheDocument()
    expect(screen.getByText('development')).toBeInTheDocument()

    // Unexposed subsystems transparency
    expect(screen.getByText(/Unexposed Integrations & Boundary Status/)).toBeInTheDocument()
    expect(screen.getByText(/PostgreSQL Database/)).toBeInTheDocument()
    expect(screen.getByText(/Docker Sandbox Daemon/)).toBeInTheDocument()
  })

  it('renders down / error badge when probe fails', async () => {
    vi.spyOn(apiClient, 'getLiveness').mockRejectedValue(new Error('Connection failed'))
    vi.spyOn(apiClient, 'getReadiness').mockRejectedValue(new Error('Connection failed'))

    render(<SystemHealthView />)

    await waitFor(() => {
      expect(screen.getByText('Health Check Failure')).toBeInTheDocument()
      expect(screen.getByText('DOWN')).toBeInTheDocument()
      expect(screen.getByText('NOT READY')).toBeInTheDocument()
    })
  })
})
