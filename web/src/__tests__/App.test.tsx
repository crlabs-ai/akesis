import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '../App'
import { apiClient } from '../services/api'
import { mockIncidentSummaryList, mockPipelineSummaryList, mockReadinessResponse } from './fixtures'

const Where = () => {
  const { pathname, search } = useLocation()
  return <div data-testid="where">{pathname + search}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
      <Where />
    </MemoryRouter>,
  )
}

describe('top-level routing', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(apiClient, 'getReadiness').mockResolvedValue(mockReadinessResponse)
    vi.spyOn(apiClient, 'getIncidents').mockResolvedValue(mockIncidentSummaryList)
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue(mockPipelineSummaryList)
  })

  it('serves the public landing page at /', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { level: 1, name: /Broken builds,\s+fixed with proof\./ })).toBeInTheDocument()
    // The public site never loads the console.
    expect(screen.queryByText('AKESIS')).not.toBeInTheDocument()
    expect(apiClient.getIncidents).not.toHaveBeenCalled()
  })

  it('serves the operator console under /app with its real navigation and data', async () => {
    renderAt('/app')
    expect(await screen.findByText('AKESIS')).toBeInTheDocument()
    for (const name of ['Overview', 'Incidents', 'Pipelines', 'System Health']) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('href', expect.stringMatching(/^\/app/))
    }
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('REPORTS READY')).toBeInTheDocument())
  })

  it.each([
    ['/incidents', '/app/incidents'],
    ['/incidents/inc_123?tab=diff', '/app/incidents/inc_123?tab=diff'],
    ['/pipelines', '/app/pipelines'],
    ['/deliveries', '/app/deliveries'],
    ['/health', '/app/health'],
  ])('redirects the old console URL %s to %s', async (from, to) => {
    renderAt(from)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(to))
  })

  it('sends unknown paths to the landing page', async () => {
    renderAt('/no-such-page')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/$/))
  })
})
