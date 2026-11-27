import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiClient, refreshSession, signInUrl } from '../services/api'
import { session } from '../services/session'
import type { Me } from '../types/api'

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const me = (overrides: Partial<Me> = {}): Me => ({
  user_id: 'usr_1',
  github_login: 'octo',
  display_name: null,
  avatar_url: null,
  session_expires_at: '2026-11-28T00:00:00Z',
  workspaces: [
    { workspace_id: 'ws_personal', kind: 'personal', name: 'Personal', slug: 'personal-1', role: 'owner' },
    { workspace_id: 'ws_team', kind: 'team', name: 'Team', slug: 'team-1', role: 'viewer' },
  ],
  ...overrides,
})

const init = (call: unknown[]): RequestInit => call[1] as RequestInit

describe('apiClient sessions, workspace scope and failure handling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    session.reset()
    window.localStorage.clear()
    session.signedIn(me())
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    session.reset()
  })

  it('scopes tenant reads to the selected workspace and sends the session cookie only', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, []))
    await apiClient.getIncidents({ limit: 5, offset: 0 })
    const [url, options] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/v1/workspaces/ws_personal/incidents?limit=5&offset=0')
    expect((options as RequestInit).credentials).toBe('include')
    expect((options as RequestInit).headers).not.toHaveProperty('X-Akesis-Read-Token')
    expect((options as RequestInit).headers).not.toHaveProperty('X-Akesis-CSRF')
  })

  it('switching workspace switches every request path', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, { items: [], next_cursor: null }))
    session.selectWorkspace('ws_team')
    await apiClient.getWebhookDeliveries()
    await apiClient.getIncidentDetail('inc/../x')
    expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/workspaces/ws_team/deliveries')
    expect(String(fetchMock.mock.calls[1][0])).toBe('/v1/workspaces/ws_team/incidents/inc%2F..%2Fx')
  })

  it('never requests tenant data without a workspace', async () => {
    session.signedOut(null)
    const failure = (await apiClient.getPipelines().catch((error: unknown) => error)) as ApiError
    expect(failure.kind).toBe('unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the CSRF header and JSON body on every state-changing request', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await apiClient.changeRole('usr_2', 'approver')
    const options = init(fetchMock.mock.calls[0])
    expect(options.method).toBe('PATCH')
    expect(options.headers).toMatchObject({ 'X-Akesis-CSRF': '1', 'Content-Type': 'application/json' })
    expect(options.body).toBe(JSON.stringify({ role: 'approver' }))
    expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/workspaces/ws_personal/members/usr_2')
  })

  it('a 401 ends the client session and keeps the reason', async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: 'session_expired' }))
    const failure = (await apiClient.getIncidents().catch((error: unknown) => error)) as ApiError
    expect(failure.kind).toBe('unauthorized')
    expect(session.getState()).toMatchObject({ status: 'signed_out', reason: 'session_expired' })
  })

  it('refreshSession reports a missing session without throwing', async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: 'not_signed_in' }))
    await expect(refreshSession()).resolves.toBe(false)
  })

  it('stores no credential in browser storage; only the workspace preference', async () => {
    session.selectWorkspace('ws_team')
    expect(window.localStorage.getItem('akesis.workspace')).toBe('ws_team')
    const stored = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage })
    expect(stored).not.toMatch(/token|session_|ghu_/i)
  })

  it('starts sign-in as a backend redirect with a same-site return path', () => {
    expect(signInUrl('/app/members')).toBe('/v1/auth/github/login?return_to=%2Fapp%2Fmembers')
  })

  it('classifies a refused connection as a network failure, not an empty result', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const failure = (await apiClient.getIncidents().catch((error: unknown) => error)) as ApiError
    expect(failure.kind).toBe('network')
    expect(failure.statusCode).toBe(0)
  })

  it('times out a request that never answers', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError'))
          )
        })
    )
    const pending = apiClient.getIncidents().catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(15_001)
    const failure = (await pending) as ApiError
    expect(failure.kind).toBe('timeout')
  })

  it('renders FastAPI validation lists as readable messages', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(422, { detail: [{ msg: 'Input should be less than or equal to 200' }] })
    )
    const failure = (await apiClient
      .getWebhookDeliveries({ limit: 999 })
      .catch((error: unknown) => error)) as ApiError
    expect(failure.kind).toBe('validation')
    expect(failure.message).toBe('Input should be less than or equal to 200')
  })

  it('reports a non-JSON success body as malformed instead of returning it', async () => {
    fetchMock.mockResolvedValue(new Response('<html>proxy error</html>', { status: 200 }))
    const failure = (await apiClient.getPipelines().catch((error: unknown) => error)) as ApiError
    expect(failure.kind).toBe('malformed')
  })

  it('repeats every requested delivery status and never sends a requeue request', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { items: [], next_cursor: null }))
    await apiClient.getWebhookDeliveries({ status: ['exhausted', 'retryable_error'], limit: 25 })
    const [url, options] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('status=exhausted')
    expect(String(url)).toContain('status=retryable_error')
    expect(String(url)).toContain('limit=25')
    expect((options as RequestInit).method ?? 'GET').toBe('GET')
  })
})
