import React, { useState } from 'react'
import { authStore } from '../../services/authStore'

interface TokenFormProps {
  onSaved?: () => void
  compact?: boolean
}

/**
 * Operator read-token entry. The value is kept only for this browser session (sessionStorage) and
 * is never written to the bundle, localStorage or the URL.
 */
export const TokenForm: React.FC<TokenFormProps> = ({ onSaved, compact = false }) => {
  const [value, setValue] = useState<string>('')

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (value.trim() === '') return
    authStore.setToken(value)
    setValue('')
    onSaved?.()
  }

  return (
    <form className={`token-form${compact ? ' is-compact' : ''}`} onSubmit={handleSubmit} autoComplete="off">
      <label className="token-form-label" htmlFor={compact ? 'read-token-compact' : 'read-token'}>
        Read token
      </label>
      <div className="token-form-row">
        <input
          id={compact ? 'read-token-compact' : 'read-token'}
          className="token-form-input"
          type="password"
          name="akesis-read-token"
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Paste the operator read token"
        />
        <button type="submit" className="btn btn-sm" disabled={value.trim() === ''}>
          Use token
        </button>
      </div>
      <p className="token-form-note">Kept for this browser session only. Closing the tab clears it.</p>
    </form>
  )
}
