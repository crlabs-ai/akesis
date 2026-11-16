/**
 * Akesis API client.
 *
 * - Base URL: VITE_API_BASE_URL when set (a public, non-secret value), otherwise same-origin
 *   relative paths, which the Vite dev proxy and a same-origin reverse proxy both serve.
 * - Authentication: the operator's read token, from the session-only authStore, is sent as the
 *   X-Akesis-Read-Token header. No credential is read from a VITE_ variable.
 * - Every request has a timeout. Failures are classified into ApiError kinds so pages can show
 *   precise states rather than turning them into empty results.
 */

import type {
  IncidentDetail,
  IncidentSummary,
  LivenessResponse,
  PipelineSummary,
  ReadinessResponse,
  WebhookDeliveryPage,
} from '../types/api'
import { authStore } from './authStore'

export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export const REQUEST_TIMEOUT_MS = 15_000
const READ_TOKEN_HEADER = 'X-Akesis-Read-Token'

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'validation'
  | 'rate_limited'
  | 'server'
  | 'malformed'
  | 'http'

export class ApiError extends Error {
  public statusCode: number
  public details?: unknown
  public kind: ApiErrorKind

  constructor(message: string, statusCode: number, details?: unknown, kind?: ApiErrorKind) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = statusCode
    this.details = details
    this.kind = kind ?? kindForStatus(statusCode)
  }
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 0) return 'network'
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  if (status === 422) return 'validation'
  if (status === 429) return 'rate_limited'
  if (status >= 500) return 'server'
  return 'http'
}

/** FastAPI returns a string for most errors and a list of messages for validation errors. */
function describeDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter((message): message is string => Boolean(message))
    return messages.length > 0 ? messages.join('; ') : undefined
  }
  return undefined
}

async function request<T>(
  path: string,
  options: { query?: URLSearchParams } = {}
): Promise<T> {
  const query = options.query?.toString()
  const url = `${API_BASE_URL}${path}${query ? `?${query}` : ''}`
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = authStore.getToken()
  if (token) {
    headers[READ_TOKEN_HEADER] = token
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(url, { headers, signal: controller.signal, cache: 'no-store' })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(
        `The backend did not answer within ${REQUEST_TIMEOUT_MS / 1000} seconds.`,
        0,
        undefined,
        'timeout'
      )
    }
    throw new ApiError(
      'The backend is unreachable. Check that the API server is running and the network is available.',
      0,
      error instanceof Error ? error.message : undefined,
      'network'
    )
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      payload = undefined
    }
    const detail = describeDetail(payload)
    const error = new ApiError(
      detail ?? fallbackMessage(response.status),
      response.status,
      payload
    )
    if (error.kind === 'unauthorized') {
      authStore.markAuthRequired()
    }
    throw error
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError('The backend returned a response that is not valid JSON.', response.status, undefined, 'malformed')
  }
}

function fallbackMessage(status: number): string {
  switch (kindForStatus(status)) {
    case 'unauthorized':
      return 'A read token is required, or the token is not accepted.'
    case 'forbidden':
      return 'This token is not allowed to view this resource.'
    case 'not_found':
      return 'The requested resource was not found.'
    case 'rate_limited':
      return 'Too many requests. Wait a moment, then refresh.'
    case 'server':
      return `The backend reported a server error (HTTP ${status}).`
    default:
      return `The backend returned HTTP ${status}.`
  }
}

function pagingQuery(params?: { limit?: number; offset?: number }): URLSearchParams {
  const query = new URLSearchParams()
  if (params?.limit !== undefined) query.set('limit', String(params.limit))
  if (params?.offset !== undefined) query.set('offset', String(params.offset))
  return query
}

export const apiClient = {
  /** Liveness: the API process answers. */
  async getLiveness(): Promise<LivenessResponse> {
    return request<LivenessResponse>('/health/liveness')
  },

  /**
   * Readiness as the backend reports it. The backend returns a constant and does not check the
   * database, so the UI must not present this as a database-backed readiness signal.
   */
  async getReadiness(): Promise<ReadinessResponse> {
    return request<ReadinessResponse>('/health/readiness')
  },

  /** Incident summaries, newest first. Excludes diff_content. */
  async getIncidents(params?: { limit?: number; offset?: number }): Promise<IncidentSummary[]> {
    return request<IncidentSummary[]>('/v1/incidents', { query: pagingQuery(params) })
  },

  /** Full incident detail, including diff_content and execution context. */
  async getIncidentDetail(incidentId: string): Promise<IncidentDetail> {
    return request<IncidentDetail>(`/v1/incidents/${encodeURIComponent(incidentId)}`)
  },

  /** Pipeline summaries, newest first. */
  async getPipelines(params?: { limit?: number; offset?: number }): Promise<PipelineSummary[]> {
    return request<PipelineSummary[]>('/v1/pipelines', { query: pagingQuery(params) })
  },

  /**
   * Webhook deliveries in operator states, keyset-paginated. Pass the previous page's
   * next_cursor to continue. Omit `status` to receive every operator state the backend returns.
   */
  async getWebhookDeliveries(params?: {
    status?: string[]
    limit?: number
    cursor?: string
  }): Promise<WebhookDeliveryPage> {
    const query = new URLSearchParams()
    for (const status of params?.status ?? []) query.append('status', status)
    if (params?.limit !== undefined) query.set('limit', String(params.limit))
    if (params?.cursor !== undefined) query.set('cursor', params.cursor)
    return request<WebhookDeliveryPage>('/v1/webhook-deliveries', { query })
  },
}
