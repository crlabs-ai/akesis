/**
 * Akesis API Client
 * Uses relative browser endpoints so it works seamlessly with Vite dev proxy
 * (port 5173 -> 8000) and production same-origin reverse proxies without leaking tokens.
 */

import type {
  IncidentDetail,
  IncidentSummary,
  LivenessResponse,
  PipelineSummary,
  ReadinessResponse,
} from '../types/api'

export class ApiError extends Error {
  public statusCode: number
  public details?: unknown

  constructor(message: string, statusCode: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.statusCode = statusCode
    this.details = details
  }
}

async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(endpoint, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options?.headers || {}),
      },
    })
  } catch (error) {
    throw new ApiError(
      `Network connection failure: ${error instanceof Error ? error.message : 'Unable to connect to backend'}`,
      0
    )
  }

  if (!response.ok) {
    let errorDetail: string | undefined
    try {
      const errorJson = await response.json()
      errorDetail = errorJson.detail || errorJson.message || JSON.stringify(errorJson)
    } catch {
      // response is not JSON
    }

    if (response.status === 401) {
      throw new ApiError(
        errorDetail || 'Unauthorized: API read token required or invalid',
        401
      )
    }
    if (response.status === 403) {
      throw new ApiError(
        errorDetail || 'Forbidden: You do not have permission to view this resource',
        403
      )
    }
    if (response.status === 404) {
      throw new ApiError(
        errorDetail || 'Resource not found',
        404
      )
    }
    if (response.status === 429) {
      throw new ApiError(
        errorDetail || 'Rate limit exceeded. Please wait before retrying.',
        429
      )
    }
    if (response.status >= 500) {
      throw new ApiError(
        errorDetail || `Internal Server Error (${response.status})`,
        response.status
      )
    }

    throw new ApiError(
      errorDetail || `HTTP error ${response.status}`,
      response.status
    )
  }

  return (await response.json()) as T
}

export const apiClient = {
  /**
   * Check API liveness
   */
  async getLiveness(): Promise<LivenessResponse> {
    return request<LivenessResponse>('/health/liveness')
  },

  /**
   * Check API readiness and environment
   */
  async getReadiness(): Promise<ReadinessResponse> {
    return request<ReadinessResponse>('/health/readiness')
  },

  /**
   * Retrieve list of incidents (excludes diff_content)
   */
  async getIncidents(params?: { limit?: number; offset?: number }): Promise<IncidentSummary[]> {
    const query = new URLSearchParams()
    if (params?.limit !== undefined) query.set('limit', String(params.limit))
    if (params?.offset !== undefined) query.set('offset', String(params.offset))
    const queryString = query.toString() ? `?${query.toString()}` : ''
    return request<IncidentSummary[]>(`/v1/incidents${queryString}`)
  },

  /**
   * Retrieve single incident detail (includes diff_content and execution context)
   */
  async getIncidentDetail(incidentId: string): Promise<IncidentDetail> {
    return request<IncidentDetail>(`/v1/incidents/${encodeURIComponent(incidentId)}`)
  },

  /**
   * Retrieve list of pipelines (excludes internal serialized JSON blobs)
   */
  async getPipelines(params?: { limit?: number; offset?: number }): Promise<PipelineSummary[]> {
    const query = new URLSearchParams()
    if (params?.limit !== undefined) query.set('limit', String(params.limit))
    if (params?.offset !== undefined) query.set('offset', String(params.offset))
    const queryString = query.toString() ? `?${query.toString()}` : ''
    return request<PipelineSummary[]>(`/v1/pipelines${queryString}`)
  },
}
