import React from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Clock, KeyRound, ShieldAlert, WifiOff } from 'lucide-react'
import { ApiError } from '../../services/api'

interface ApiErrorPanelProps {
  error: unknown
  onRetry?: () => void
  /** Short label for what failed, e.g. "incidents". */
  subject?: string
}

interface Presentation {
  title: string
  icon: React.ReactNode
  guidance: string
  showSignIn?: boolean
}

function present(error: unknown, subject: string): Presentation {
  if (!(error instanceof ApiError)) {
    return {
      title: `Could not load ${subject}`,
      icon: <AlertTriangle size={16} aria-hidden="true" />,
      guidance: 'An unexpected error occurred while loading this data.',
    }
  }
  switch (error.kind) {
    case 'unauthorized':
      return {
        title: 'Sign in required',
        icon: <KeyRound size={16} aria-hidden="true" />,
        guidance: 'Your session has ended or was signed out. Sign in with GitHub to continue.',
        showSignIn: true,
      }
    case 'forbidden':
      return {
        title: 'Access denied',
        icon: <ShieldAlert size={16} aria-hidden="true" />,
        guidance:
          'Your role in this workspace does not allow this. A workspace owner or admin can change it.',
      }
    case 'network':
      return {
        title: 'Backend unreachable',
        icon: <WifiOff size={16} aria-hidden="true" />,
        guidance:
          'Nothing answered. Check that the API server is running and that the browser can reach it. Nothing is shown as empty data.',
      }
    case 'timeout':
      return {
        title: 'Backend did not respond in time',
        icon: <Clock size={16} aria-hidden="true" />,
        guidance: 'The request timed out. The backend may be busy or waiting on the database.',
      }
    case 'validation':
      return {
        title: 'Request rejected',
        icon: <AlertTriangle size={16} aria-hidden="true" />,
        guidance: 'The backend rejected the request parameters. This is a client-side bug, not missing data.',
      }
    case 'rate_limited':
      return {
        title: 'Rate limited',
        icon: <Clock size={16} aria-hidden="true" />,
        guidance: 'The backend asked the client to slow down. Wait a moment, then refresh.',
      }
    case 'server':
      return {
        title: 'Backend error',
        icon: <AlertTriangle size={16} aria-hidden="true" />,
        guidance: 'The backend failed while handling the request. The data is unavailable, not empty.',
      }
    case 'not_found':
      return {
        title: 'Not found',
        icon: <AlertTriangle size={16} aria-hidden="true" />,
        guidance: 'The record does not exist in this workspace, or you are not a member of it.',
      }
    case 'malformed':
      return {
        title: 'Unexpected response',
        icon: <AlertTriangle size={16} aria-hidden="true" />,
        guidance: 'The backend answered with data this page cannot read.',
      }
    default:
      return {
        title: `Could not load ${subject}`,
        icon: <AlertTriangle size={16} aria-hidden="true" />,
        guidance: 'The backend returned an unexpected status.',
      }
  }
}

/**
 * Renders a failure as a distinct state. Pages use it in place of an empty table, so a failed
 * request is never mistaken for "no records".
 */
export const ApiErrorPanel: React.FC<ApiErrorPanelProps> = ({ error, onRetry, subject = 'data' }) => {
  const view = present(error, subject)
  const detail = error instanceof ApiError ? error.message : undefined
  return (
    <div className={`api-error api-error-${error instanceof ApiError ? error.kind : 'unknown'}`} role="alert">
      <div className="api-error-head">
        {view.icon}
        <strong>{view.title}</strong>
      </div>
      <p className="api-error-guidance">{view.guidance}</p>
      {detail && <p className="api-error-detail mono">{detail}</p>}
      {view.showSignIn && (
        <Link className="btn btn-sm" to="/login">
          Sign in with GitHub
        </Link>
      )}
      {onRetry && !view.showSignIn && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}
