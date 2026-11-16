import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SystemHealthView } from '../components/Health/SystemHealthView'
import { ApiError, apiClient } from '../services/api'
import { mockReadinessResponse } from './fixtures'

afterEach(() => vi.restoreAllMocks())

describe('System health honesty', () => {
  it('does not keep showing an earlier successful probe after a failed one', async () => {
    const liveness = vi
      .spyOn(apiClient, 'getLiveness')
      .mockResolvedValueOnce({ status: 'ok' })
      .mockRejectedValueOnce(new ApiError('down', 0, undefined, 'network'))
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    render(<SystemHealthView />)
    await waitFor(() => expect(screen.getByText('Probes Active')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /check health/i }))
    await waitFor(() => expect(screen.getByText('Probe failed')).toBeInTheDocument())
    expect(screen.queryByText('Probes Active')).not.toBeInTheDocument()
    expect(screen.queryByText('OK')).not.toBeInTheDocument()
    expect(liveness).toHaveBeenCalledTimes(2)
  })

  it('states that readiness does not check the database and that the worker is not reported', async () => {
    vi.spyOn(apiClient, 'getLiveness').mockResolvedValue({ status: 'ok' })
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    render(<SystemHealthView />)
    await waitFor(() => expect(screen.getByText('Probes Active')).toBeInTheDocument())
    expect(screen.getAllByText(/does not check the database/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Not reported by the API/)).toBeInTheDocument()
  })
})
