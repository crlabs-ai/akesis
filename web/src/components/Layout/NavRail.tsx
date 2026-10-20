import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { gsap } from 'gsap'
import { Activity, AlertOctagon, GitMerge, LayoutDashboard, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react'

const STORAGE_KEY = 'akesis.sidebar.expanded'
const EXPANDED_WIDTH = 224
const COLLAPSED_WIDTH = 56
const MOBILE_QUERY = '(max-width: 860px)'

const readPreference = (): boolean => {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === null ? true : stored === 'true'
  } catch {
    return true
  }
}

const writePreference = (expanded: boolean) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(expanded))
  } catch {
    // Storage unavailable (private mode or blocked); the preference simply isn't kept.
  }
}

const matches = (query: string): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches

const prefersReducedMotion = (): boolean => matches('(prefers-reduced-motion: reduce)')

const ROUTES = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/incidents', label: 'Incidents', icon: AlertOctagon, end: false },
  { to: '/pipelines', label: 'Pipelines', icon: GitMerge, end: false },
  { to: '/health', label: 'System Health', icon: Activity, end: false },
]

export const NavRail: React.FC = () => {
  const [expanded, setExpanded] = useState<boolean>(readPreference)
  const [isMobile, setIsMobile] = useState<boolean>(() => matches(MOBILE_QUERY))
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false)
  const asideRef = useRef<HTMLElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  // Track breakpoint: below it the sidebar becomes a drawer
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(MOBILE_QUERY)
    const onChange = () => setIsMobile(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  // Desktop width transition (GSAP). Reduced motion applies the width instantly.
  useLayoutEffect(() => {
    const el = asideRef.current
    if (!el || isMobile) return
    const target = expanded ? EXPANDED_WIDTH : COLLAPSED_WIDTH
    const tween = gsap.to(el, {
      width: target,
      duration: prefersReducedMotion() ? 0 : 0.18,
      ease: 'power2.out',
      overwrite: 'auto',
    })
    return () => {
      tween.kill()
    }
  }, [expanded, isMobile])

  // Drawer: slide in with GSAP, close on Escape, return focus to the trigger
  useEffect(() => {
    if (!isMobile || !drawerOpen) return
    const panel = drawerRef.current
    if (panel) {
      const tween = gsap.fromTo(
        panel,
        { x: -24, opacity: 0 },
        { x: 0, opacity: 1, duration: prefersReducedMotion() ? 0 : 0.16, ease: 'power2.out' }
      )
      const firstLink = panel.querySelector<HTMLElement>('a')
      firstLink?.focus()
      const onKey = (e: KeyboardEvent) => {
        if (e.key === 'Escape') closeDrawer()
      }
      document.addEventListener('keydown', onKey)
      return () => {
        document.removeEventListener('keydown', onKey)
        tween.kill()
      }
    }
  }, [isMobile, drawerOpen])

  const toggle = () => {
    setExpanded((prev) => {
      writePreference(!prev)
      return !prev
    })
  }

  const closeDrawer = () => {
    setDrawerOpen(false)
    // Return focus to the control that opened the drawer so keyboard users keep their place
    requestAnimationFrame(() => triggerRef.current?.focus())
  }

  const renderLinks = (showLabels: boolean, onNavigate?: () => void) => (
    <nav className="nav-links" aria-label="Primary">
      {ROUTES.map((route) => {
        const Icon = route.icon
        return (
          <NavLink
            key={route.to}
            to={route.to}
            end={route.end}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''} ${showLabels ? 'is-labelled' : ''}`}
            title={route.label}
            aria-label={route.label}
            onClick={onNavigate}
          >
            <Icon size={18} aria-hidden="true" />
            {showLabels && <span className="nav-item-label">{route.label}</span>}
          </NavLink>
        )
      })}
    </nav>
  )

  if (isMobile) {
    return (
      <>
        <button
          ref={triggerRef}
          type="button"
          className="nav-drawer-trigger btn btn-sm btn-icon"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open navigation"
          aria-expanded={drawerOpen}
          aria-controls="nav-drawer"
        >
          <PanelLeftOpen size={15} aria-hidden="true" />
        </button>

        {drawerOpen && (
          <div className="nav-drawer-scrim" onClick={closeDrawer} aria-hidden="true" />
        )}
        {drawerOpen && (
          <aside id="nav-drawer" className="nav-drawer" aria-label="Primary Navigation" ref={drawerRef}>
            <div className="nav-brand nav-brand-row">
              <span className="brand-mark" aria-hidden="true">A</span>
              <span className="nav-brand-name">Navigation</span>
              <button type="button" className="btn btn-sm btn-icon nav-close" onClick={closeDrawer} aria-label="Close navigation">
                <X size={14} aria-hidden="true" />
              </button>
            </div>
            {renderLinks(true, closeDrawer)}
          </aside>
        )}
      </>
    )
  }

  return (
    <aside
      ref={asideRef}
      className={`nav-rail ${expanded ? 'is-expanded' : 'is-collapsed'}`}
      aria-label="Primary Navigation"
      style={{ width: expanded ? EXPANDED_WIDTH : COLLAPSED_WIDTH }}
    >
      <div className="nav-brand nav-brand-row">
        <span className="brand-mark" title="Akesis — CRLabs CI/CD Control Plane" aria-hidden="true">A</span>
      </div>

      {renderLinks(expanded)}

      <button
        type="button"
        className="nav-toggle"
        onClick={toggle}
        aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        aria-expanded={expanded}
        title={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
      >
        {expanded ? <PanelLeftClose size={16} aria-hidden="true" /> : <PanelLeftOpen size={16} aria-hidden="true" />}
        {expanded && <span>Collapse</span>}
      </button>
    </aside>
  )
}
