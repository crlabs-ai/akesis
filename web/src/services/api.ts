/**
 * Akesis API client.
 *
 * - Base URL: VITE_API_BASE_URL when set (a public, non-secret value), otherwise same-origin
 *   relative paths, which the Vite dev proxy and a same-origin reverse proxy both serve.
 * - Authentication: the HttpOnly session cookie set by GitHub sign-in (ADR-0012). Browser code
 *   never sees it. Every state-changing request carries X-Akesis-CSRF, which the backend
 *   requires; a cross-site page cannot send it without a CORS preflight the backend refuses.
 * - Tenancy: every incident, pipeline and delivery request is scoped to the selected workspace
 *   (/v1/workspaces/{id}/...). The ID selects; the backend authorizes membership every time.
 * - Every request has a timeout. Failures are classified into ApiError kinds so pages can show
 *   precise states rather than turning them into empty results.
 */

import type {
  GitHubInstallation,
  IncidentDetail,
  IncidentSummary,
  LivenessResponse,
  Me,
  PipelineSummary,
  ReadinessResponse,
  WebhookDeliveryPage,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRepository,
  WorkspaceRole,
  WorkspaceSummary,
} from '../types/api'
import { session } from './session'

export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export const REQUEST_TIMEOUT_MS = 15_000
const CSRF_HEADER = 'X-Akesis-CSRF'

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

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

async function request<T>(
  path: string,
  options: { query?: URLSearchParams; method?: Method; body?: unknown } = {}
): Promise<T> {
  const query = options.query?.toString()
  const url = `${API_BASE_URL}${path}${query ? `?${query}` : ''}`
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') headers[CSRF_HEADER] = '1'
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'include',
      signal: controller.signal,
      cache: 'no-store',
    })
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
      session.signedOut(typeof detail === 'string' ? detail : null)
    }
    throw error
  }

  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError('The backend returned a response that is not valid JSON.', response.status, undefined, 'malformed')
  }
}

function fallbackMessage(status: number): string {
  switch (kindForStatus(status)) {
    case 'unauthorized':
      return 'Sign in to continue.'
    case 'forbidden':
      return 'Your role in this workspace does not allow this.'
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

/** The selected workspace's API prefix. Tenant data is never requested without one. */
function ws(): string {
  const id = session.workspaceId()
  if (!id) throw new ApiError('Select a workspace first.', 401, undefined, 'unauthorized')
  return `/v1/workspaces/${encodeURIComponent(id)}`
}

const enc = encodeURIComponent

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
    return request<IncidentSummary[]>(`${ws()}/incidents`, { query: pagingQuery(params) })
  },

  /** Full incident detail, including diff_content and execution context. */
  async getIncidentDetail(incidentId: string): Promise<IncidentDetail> {
    return request<IncidentDetail>(`${ws()}/incidents/${enc(incidentId)}`)
  },

  /** Pipeline summaries, newest first. */
  async getPipelines(params?: { limit?: number; offset?: number }): Promise<PipelineSummary[]> {
    return request<PipelineSummary[]>(`${ws()}/pipelines`, { query: pagingQuery(params) })
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
    return request<WebhookDeliveryPage>(`${ws()}/deliveries`, { query })
  },

  // --- Identity and sessions ----------------------------------------------------------------

  /** The signed-in user and their workspaces; 401 when signed out. */
  async getMe(): Promise<Me> {
    return request<Me>('/v1/me')
  },

  /** Exchange GitHub's code and state for a session cookie. Returns where to go next. */
  async completeSignIn(code: string, state: string): Promise<{ return_to: string }> {
    return request('/v1/auth/github/callback', { method: 'POST', body: { code, state } })
  },

  async signOut(): Promise<void> {
    return request('/v1/auth/logout', { method: 'POST' })
  },

  async signOutEverywhere(): Promise<void> {
    return request('/v1/auth/sessions/revoke-all', { method: 'POST' })
  },

  // --- Workspaces -----------------------------------------------------------------------------

  async createWorkspace(name: string): Promise<WorkspaceSummary> {
    return request('/v1/workspaces', { method: 'POST', body: { name } })
  },

  async renameWorkspace(name: string): Promise<void> {
    return request(ws(), { method: 'PATCH', body: { name } })
  },

  async leaveWorkspace(): Promise<void> {
    return request(`${ws()}/leave`, { method: 'POST' })
  },

  async getMembers(): Promise<WorkspaceMember[]> {
    return request(`${ws()}/members`)
  },

  async changeRole(userId: string, role: WorkspaceRole): Promise<void> {
    return request(`${ws()}/members/${enc(userId)}`, { method: 'PATCH', body: { role } })
  },

  async setSlackUser(userId: string, slackUserId: string | null): Promise<void> {
    return request(`${ws()}/members/${enc(userId)}/slack`, {
      method: 'PUT',
      body: { slack_user_id: slackUserId },
    })
  },

  async removeMember(userId: string): Promise<void> {
    return request(`${ws()}/members/${enc(userId)}`, { method: 'DELETE' })
  },

  async getInvitations(): Promise<WorkspaceInvitation[]> {
    return request(`${ws()}/invitations`)
  },

  async invite(githubLogin: string, role: WorkspaceRole): Promise<WorkspaceInvitation> {
    return request(`${ws()}/invitations`, {
      method: 'POST',
      body: { github_login: githubLogin, role },
    })
  },

  async revokeInvitation(invitationId: string): Promise<void> {
    return request(`${ws()}/invitations/${enc(invitationId)}`, { method: 'DELETE' })
  },

  async getMyInvitations(): Promise<WorkspaceInvitation[]> {
    return request('/v1/me/invitations')
  },

  async acceptInvitation(invitationId: string): Promise<WorkspaceSummary> {
    return request(`/v1/me/invitations/${enc(invitationId)}/accept`, { method: 'POST' })
  },

  // --- GitHub installations and repositories ------------------------------------------------

  async getInstallations(): Promise<GitHubInstallation[]> {
    return request(`${ws()}/installations`)
  },

  /** Installations of the Akesis App this user can access on GitHub (checked server-side). */
  async getAvailableInstallations(): Promise<GitHubInstallation[]> {
    return request(`${ws()}/installations/available`)
  },

  async linkInstallation(installationId: number): Promise<GitHubInstallation> {
    return request(`${ws()}/installations`, {
      method: 'POST',
      body: { installation_id: installationId },
    })
  },

  async unlinkInstallation(installationId: number): Promise<void> {
    return request(`${ws()}/installations/${installationId}`, { method: 'DELETE' })
  },

  async syncInstallation(installationId: number): Promise<{ repositories: number }> {
    return request(`${ws()}/installations/${installationId}/sync`, { method: 'POST' })
  },

  async getRepositories(): Promise<WorkspaceRepository[]> {
    return request(`${ws()}/repositories`)
  },
}

/** Where the browser goes to start GitHub sign-in (a full-page navigation, not fetch). */
export function signInUrl(returnTo?: string): string {
  const query = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : ''
  return `${API_BASE_URL}/v1/auth/github/login${query}`
}

/** Load /v1/me into the session store. Resolves to whether a session exists. */
export async function refreshSession(): Promise<boolean> {
  try {
    session.signedIn(await apiClient.getMe())
    return true
  } catch (error) {
    if (error instanceof ApiError && error.kind === 'unauthorized') return false
    throw error
  }
}
