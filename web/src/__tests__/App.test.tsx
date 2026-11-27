import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '../App'
import { ApiError, apiClient } from '../services/api'
import { session } from '../services/session'
import type { Me } from '../types/api'
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

const ME: Me = {
  user_id: 'usr_1',
  github_login: 'octo',
  display_name: 'Octo',
  avatar_url: null,
  session_expires_at: '2026-11-28T00:00:00Z',
  workspaces: [{ workspace_id: 'ws_1', kind: 'personal', name: 'Personal', slug: 'personal-1', role: 'owner' }],
}

describe('top-level routing', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    session.reset()
    vi.spyOn(apiClient, 'getMe').mockResolvedValue(ME)
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

  it('sends a signed-out visitor from the console to sign-in, keeping the destination', async () => {
    vi.spyOn(apiClient, 'getMe').mockRejectedValue(new ApiError('not_signed_in', 401))
    renderAt('/app/members')
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/login?return_to=%2Fapp%2Fmembers')
    )
    expect(apiClient.getIncidents).not.toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: 'Continue with GitHub' })).toBeInTheDocument()
  })

  it('explains a cancelled GitHub sign-in', async () => {
    renderAt('/login?error=access_denied')
    expect(await screen.findByRole('alert')).toHaveTextContent('GitHub sign-in was cancelled')
  })

  it('rejects a callback without code and state before calling the backend', async () => {
    const complete = vi.spyOn(apiClient, 'completeSignIn')
    renderAt('/auth/callback?error=access_denied')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/login?error=access_denied'))
    expect(complete).not.toHaveBeenCalled()
  })

  it('completes sign-in once and lands on the returned console path', async () => {
    const complete = vi
      .spyOn(apiClient, 'completeSignIn')
      .mockResolvedValue({ return_to: '/app/incidents' })
    renderAt('/auth/callback?code=abc&state=xyz')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/app/incidents'))
    expect(complete).toHaveBeenCalledTimes(1)
    expect(complete).toHaveBeenCalledWith('abc', 'xyz')
  })

  it('sends a backend sign-in refusal back to the login page with its code', async () => {
    vi.spyOn(apiClient, 'completeSignIn').mockRejectedValue(new ApiError('invalid_state', 400))
    renderAt('/auth/callback?code=abc&state=forged')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/login?error=invalid_state'))
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
