import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { RefreshCw, Search } from 'lucide-react'
import { AccessControl } from '../Access/AccessControl'
import { openCommandPalette } from '../../lib/paletteEvents'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

interface HeaderProps {
  onRefresh?: () => void
  isRefreshing?: boolean
}

export const Header: React.FC<HeaderProps> = ({ onRefresh, isRefreshing = false }) => {
  const location = useLocation()
  const pathname = location.pathname

  const getBreadcrumbs = () => {
    if (pathname === '/') {
      return [{ label: 'Overview', to: '/' }]
    }
    if (pathname.startsWith('/incidents/')) {
      const incidentId = pathname.replace('/incidents/', '')
      return [
        { label: 'Incidents', to: '/incidents' },
        { label: incidentId, to: pathname },
      ]
    }
    if (pathname === '/incidents') {
      return [{ label: 'Incidents', to: '/incidents' }]
    }
    if (pathname === '/pipelines') {
      return [{ label: 'Pipelines', to: '/pipelines' }]
    }
    if (pathname === '/deliveries') {
      return [{ label: 'Webhook deliveries', to: '/deliveries' }]
    }
    if (pathname === '/health') {
      return [{ label: 'System Health', to: '/health' }]
    }
    return [{ label: 'Console', to: '/' }]
  }

  const breadcrumbs = getBreadcrumbs()

  return (
    <header className="app-header">
      <div className="header-left">
        <div className="header-title-group">
          <span className="header-org">CRLabs</span>
          <span className="header-title">AKESIS</span>
        </div>

        <nav className="breadcrumbs" aria-label="Breadcrumbs">
          <span className="breadcrumb-sep">/</span>
          {breadcrumbs.map((crumb, idx) => (
            <React.Fragment key={crumb.to}>
              {idx > 0 && <span className="breadcrumb-sep">/</span>}
              {idx === breadcrumbs.length - 1 ? (
                <span className="mono" style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                  {crumb.label}
                </span>
              ) : (
                <Link to={crumb.to} style={{ color: 'var(--text-muted)' }}>
                  {crumb.label}
                </Link>
              )}
            </React.Fragment>
          ))}
        </nav>
      </div>

      <div className="header-right">
        <button
          type="button"
          className="btn btn-sm palette-trigger"
          onClick={openCommandPalette}
          aria-label="Jump to a page (command palette)"
          title={`Jump to a page (${isMac ? '⌘' : 'Ctrl'}K)`}
        >
          <Search size={13} aria-hidden="true" />
          <span className="palette-trigger-label">Jump to…</span>
          <kbd className="palette-kbd">{isMac ? '⌘' : 'Ctrl'} K</kbd>
        </button>
        <AccessControl />
        {onRefresh && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={onRefresh}
            disabled={isRefreshing}
            title="Refresh current view"
            aria-label="Refresh view"
          >
            <RefreshCw size={13} className={isRefreshing ? 'spinner' : ''} />
            <span>{isRefreshing ? 'Refreshing...' : 'Refresh'}</span>
          </button>
        )}
      </div>
    </header>
  )
}
