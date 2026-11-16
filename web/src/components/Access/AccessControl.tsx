import React, { useState, useSyncExternalStore } from 'react'
import { KeyRound } from 'lucide-react'
import { authStore } from '../../services/authStore'
import { TokenForm } from './TokenForm'

/** Header control showing whether this session holds a read token. */
export const AccessControl: React.FC = () => {
  const token = useSyncExternalStore(authStore.subscribe, authStore.getToken, authStore.getToken)
  const authRequired = useSyncExternalStore(
    authStore.subscribe,
    authStore.isAuthRequired,
    authStore.isAuthRequired
  )
  const [open, setOpen] = useState<boolean>(false)
  const hasToken = token !== null

  return (
    <div className="access-control">
      <button
        type="button"
        className={`btn btn-sm access-control-trigger${authRequired ? ' is-attention' : ''}`}
        aria-expanded={open}
        aria-controls="access-control-panel"
        onClick={() => setOpen((prev) => !prev)}
      >
        <KeyRound size={13} aria-hidden="true" />
        <span>
          {hasToken ? 'Read token set' : 'No read token'}
          {authRequired ? ' · rejected' : ''}
        </span>
      </button>
      {open && (
        <div id="access-control-panel" className="access-control-panel" role="dialog" aria-label="Read token">
          <TokenForm onSaved={() => setOpen(false)} compact />
          {hasToken && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                authStore.clearToken()
                setOpen(false)
              }}
            >
              Clear token from this session
            </button>
          )}
        </div>
      )}
    </div>
  )
}
