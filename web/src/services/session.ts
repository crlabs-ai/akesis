/**
 * Signed-in state for the browser (Phase 2, ADR-0012).
 *
 * The session credential is an HttpOnly cookie: browser code can neither read nor store it. This
 * store only remembers what GET /v1/me returned and which workspace is selected. The selected
 * workspace ID is a preference, never an authorization credential: the backend checks
 * membership on every request.
 */
import type { Me, WorkspaceSummary } from '../types/api'

export type SessionStatus = 'loading' | 'signed_in' | 'signed_out'

export interface SessionState {
  status: SessionStatus
  me: Me | null
  workspace: WorkspaceSummary | null
  /** Why the session ended, when the backend said (session_expired, session_revoked...). */
  reason: string | null
}

const WORKSPACE_KEY = 'akesis.workspace'
const listeners = new Set<() => void>()
let state: SessionState = { status: 'loading', me: null, workspace: null, reason: null }

function emit(next: SessionState): void {
  state = next
  listeners.forEach((listener) => listener())
}

function storedWorkspace(): string | null {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY)
  } catch {
    return null
  }
}

function pickWorkspace(me: Me, preferred: string | null): WorkspaceSummary | null {
  return me.workspaces.find((w) => w.workspace_id === preferred) ?? me.workspaces[0] ?? null
}

export const session = {
  getState(): SessionState {
    return state
  },

  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  /** The workspace every tenant request is scoped to. */
  workspaceId(): string | null {
    return state.workspace?.workspace_id ?? null
  },

  signedIn(me: Me): void {
    const preferred = state.workspace?.workspace_id ?? storedWorkspace()
    emit({ status: 'signed_in', me, workspace: pickWorkspace(me, preferred), reason: null })
  },

  selectWorkspace(workspaceId: string): void {
    if (!state.me) return
    const workspace = pickWorkspace(state.me, workspaceId)
    try {
      if (workspace) window.localStorage.setItem(WORKSPACE_KEY, workspace.workspace_id)
    } catch {
      // Preference only; nothing breaks without storage.
    }
    emit({ ...state, workspace })
  },

  /** Called on any 401: the cookie is missing, expired or revoked. */
  signedOut(reason: string | null = null): void {
    if (state.status === 'signed_out' && state.reason === reason) return
    emit({ status: 'signed_out', me: null, workspace: null, reason })
  },

  /** Test support. */
  reset(): void {
    state = { status: 'loading', me: null, workspace: null, reason: null }
    listeners.clear()
  },
}
