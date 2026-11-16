import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { CornerDownLeft, Search } from 'lucide-react'
import { ALL_ROUTES, ROUTE_GROUPS, type AppRoute } from '../../lib/routes'
import { PALETTE_OPEN_EVENT } from '../../lib/paletteEvents'

const groupFor = (route: AppRoute): string =>
  ROUTE_GROUPS.find((group) => group.routes.includes(route))?.label ?? ''

const matches = (route: AppRoute, query: string): boolean => {
  if (query === '') return true
  const haystack = [route.label, groupFor(route), ...route.keywords].join(' ').toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .every((term) => haystack.includes(term))
}

/**
 * Keyboard launcher for the existing routes. It can only navigate: it runs no backend action and
 * shows no data, so it cannot present anything the rest of the app does not already show.
 */
export const CommandPalette: React.FC = () => {
  const [open, setOpen] = useState<boolean>(false)
  const [query, setQuery] = useState<string>('')
  const [active, setActive] = useState<number>(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const returnFocusTo = useRef<HTMLElement | null>(null)
  const navigate = useNavigate()
  const location = useLocation()
  const listId = useId()
  const titleId = useId()

  // Label matches rank above matches that only hit a group name or keyword, so "health" puts
  // System Health first rather than Deliveries (which sits in the "Delivery & health" group).
  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const filtered = ALL_ROUTES.filter((route) => matches(route, needle))
    const labelHit = (route: AppRoute) => route.label.toLowerCase().includes(needle) ? 0 : 1
    return [...filtered].sort((a, b) => labelHit(a) - labelHit(b))
  }, [query])

  // Ctrl+K / ⌘K toggles; the header button and any caller use the window event.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((prev) => !prev)
      }
    }
    const onOpen = () => setOpen(true)
    document.addEventListener('keydown', onKey)
    window.addEventListener(PALETTE_OPEN_EVENT, onOpen)
    return () => {
      document.removeEventListener('keydown', onKey)
      window.removeEventListener(PALETTE_OPEN_EVENT, onOpen)
    }
  }, [])

  // Close on route change, so a palette left open never lingers over the next page.
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!open) return
    returnFocusTo.current = document.activeElement as HTMLElement | null
    setQuery('')
    setActive(0)
    requestAnimationFrame(() => inputRef.current?.focus())
    return () => {
      returnFocusTo.current?.focus()
    }
  }, [open])

  if (!open) return null

  const choose = (route: AppRoute) => {
    setOpen(false)
    navigate(route.to)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((index) => (results.length === 0 ? 0 : (index + 1) % results.length))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => (results.length === 0 ? 0 : (index - 1 + results.length) % results.length))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const route = results[active]
      if (route) choose(route)
    } else if (event.key === 'Tab') {
      // The only focusable control is the input, so Tab stays inside the dialog.
      event.preventDefault()
    }
  }

  const optionId = (index: number) => `${listId}-opt-${index}`
  const activeRoute = results[active]

  return (
    <div className="palette-layer">
      <button type="button" className="palette-scrim" aria-label="Close command palette" tabIndex={-1} onClick={() => setOpen(false)} />
      <div ref={dialogRef} className="palette glass" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="sr-only">Jump to a page</h2>
        <div className="palette-input-row">
          <Search size={15} aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeRoute ? optionId(active) : undefined}
            placeholder="Jump to a page…"
            value={query}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => {
              setQuery(event.target.value)
              setActive(0)
            }}
            onKeyDown={onKeyDown}
          />
          <kbd className="palette-kbd">Esc</kbd>
        </div>

        {results.length === 0 ? (
          <p className="palette-empty" role="status">
            No page matches “{query}”. Try “incidents”, “deliveries” or “health”.
          </p>
        ) : (
          <ul id={listId} role="listbox" aria-label="Pages" className="palette-list">
            {results.map((route, index) => {
              const Icon = route.icon
              const isActive = index === active
              return (
                <li
                  key={route.to}
                  id={optionId(index)}
                  role="option"
                  aria-selected={isActive}
                  className={`palette-option${isActive ? ' is-active' : ''}`}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(route)}
                >
                  <Icon size={15} aria-hidden="true" />
                  <span className="palette-option-label">{route.label}</span>
                  <span className="palette-option-group">{groupFor(route)}</span>
                  {isActive && <CornerDownLeft size={13} aria-hidden="true" className="palette-enter" />}
                </li>
              )
            })}
          </ul>
        )}

        <footer className="palette-foot">
          <span><kbd className="palette-kbd">↑</kbd><kbd className="palette-kbd">↓</kbd> move</span>
          <span><kbd className="palette-kbd">↵</kbd> open</span>
          <span><kbd className="palette-kbd">Esc</kbd> close</span>
        </footer>
      </div>
    </div>
  )
}
