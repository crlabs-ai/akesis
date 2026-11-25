import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiClient } from '../services/api'
import { authStore } from '../services/authStore'

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('apiClient authentication and failure handling', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    authStore.reset()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    authStore.reset()
  })

  it('sends no read-token header when the session holds no token', async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, []))
    await apiClient.getIncidents({ limit: 5, offset: 0 })
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-Akesis-Read-Token')
  })

  it('sends the session read token as X-Akesis-Read-Token and never in the URL', async () => {
    authStore.setToken('  session-token-value  ')
    fetchMock.mockResolvedValue(jsonResponse(200, []))
    await apiClient.getPipelines({ limit: 5, offset: 0 })
    const [url, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({ 'X-Akesis-Read-Token': 'session-token-value' })
    expect(String(url)).not.toContain('session-token-value')
  })

  it('classifies 401 as unauthorized and asks the UI for a token', async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: 'Invalid or missing read API token' }))
    const failure = await apiClient.getIncidents().catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).kind).toBe('unauthorized')
    expect((failure as ApiError).message).toBe('Invalid or missing read API token')
    expect(authStore.isAuthRequired()).toBe(true)
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
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
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
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('status=exhausted')
    expect(String(url)).toContain('status=retryable_error')
    expect(String(url)).toContain('limit=25')
    expect((init as RequestInit).method ?? 'GET').toBe('GET')
  })
})

describe('authStore', () => {
  beforeEach(() => authStore.reset())
  afterEach(() => authStore.reset())

  it('keeps the token in sessionStorage only, never localStorage', () => {
    authStore.setToken('tok-123')
    expect(window.sessionStorage.getItem('akesis.readToken')).toBe('tok-123')
    expect(window.localStorage.getItem('akesis.readToken')).toBeNull()
  })

  it('clears the token completely on request', () => {
    authStore.setToken('tok-123')
    authStore.clearToken()
    expect(authStore.hasToken()).toBe(false)
    expect(window.sessionStorage.getItem('akesis.readToken')).toBeNull()
  })

  it('treats a blank entry as no token', () => {
    authStore.setToken('   ')
    expect(authStore.hasToken()).toBe(false)
  })
})
