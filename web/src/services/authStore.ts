/**
 * Session-scoped store for the operator read token.
 *
 * The token is entered by the operator at runtime. It is never read from a VITE_ variable, which
 * would be published in the bundle. It lives in sessionStorage, so it is cleared when the browser
 * tab closes, and it is never written to localStorage. Storage failures (private mode, blocked
 * storage) degrade to an in-memory token for the page's lifetime.
 */

const STORAGE_KEY = 'akesis.readToken'

type Listener = () => void

const listeners = new Set<Listener>()
let memoryToken: string | null = null
let authRequired = false
let initialised = false

function readStored(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStored(value: string | null): void {
  try {
    if (value === null) {
      window.sessionStorage.removeItem(STORAGE_KEY)
    } else {
      window.sessionStorage.setItem(STORAGE_KEY, value)
    }
  } catch {
    // Storage unavailable: the in-memory token below still applies for this page.
  }
}

function emit(): void {
  listeners.forEach((listener) => listener())
}

function current(): string | null {
  if (!initialised) {
    initialised = true
    memoryToken = readStored()
  }
  return memoryToken
}

export const authStore = {
  getToken(): string | null {
    return current()
  },

  hasToken(): boolean {
    return current() !== null
  },

  setToken(value: string): void {
    const trimmed = value.trim()
    memoryToken = trimmed === '' ? null : trimmed
    initialised = true
    writeStored(memoryToken)
    authRequired = false
    emit()
  },

  clearToken(): void {
    memoryToken = null
    initialised = true
    writeStored(null)
    emit()
  },

  /** Called when the backend answers 401. The UI then asks for a token. */
  markAuthRequired(): void {
    if (!authRequired) {
      authRequired = true
      emit()
    }
  },

  isAuthRequired(): boolean {
    return authRequired
  },

  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  /** Test support: restore the initial state. */
  reset(): void {
    memoryToken = null
    initialised = false
    authRequired = false
    listeners.clear()
    try {
      window.sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
  },
}
