import React, { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

interface DrawerProps {
  open: boolean
  title: string
  onClose: () => void
  children: React.ReactNode
}

/**
 * Right-side detail drawer. Focus moves into the drawer on open and returns to the element that
 * opened it on close. Escape closes it. The panel is a modal dialog, so the page behind is inert.
 */
export const Drawer: React.FC<DrawerProps> = ({ open, title, onClose, children }) => {
  const panelRef = useRef<HTMLElement>(null)
  const returnFocusTo = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    returnFocusTo.current = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      // Keep focus inside the modal: wrap Tab and Shift+Tab between its first and last control.
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input, [tabindex="0"]')
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const current = document.activeElement as HTMLElement | null
      if (event.shiftKey && (current === first || current === panelRef.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && current === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      returnFocusTo.current?.focus()
    }
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="drawer-layer">
      <button type="button" className="drawer-scrim" aria-label="Close details" onClick={onClose} tabIndex={-1} />
      <aside ref={panelRef} className="drawer-panel" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
        <header className="drawer-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-sm btn-icon" onClick={onClose} aria-label="Close">
            <X size={14} aria-hidden="true" />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  )
}
