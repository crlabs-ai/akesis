import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Drawer } from '../components/Common/Drawer'

describe('Drawer focus', () => {
  it('keeps Tab inside the dialog, wrapping from the last control to the first', () => {
    render(
      <>
        <button type="button">Behind page</button>
        <Drawer open title="Details" onClose={vi.fn()}>
          <button type="button">First action</button>
          <button type="button">Last action</button>
        </Drawer>
      </>
    )
    const last = screen.getByRole('button', { name: 'Last action' })
    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    // The first focusable inside the dialog is the header close button, which precedes the content.
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }))
  })

  it('wraps Shift+Tab from the first control back to the last', () => {
    render(
      <Drawer open title="Details" onClose={vi.fn()}>
        <button type="button">Only action</button>
      </Drawer>
    )
    const close = screen.getByRole('button', { name: 'Close' })
    close.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Only action' }))
  })

  it('closes on Escape', () => {
    const onClose = vi.fn()
    render(
      <Drawer open title="Details" onClose={onClose}>
        <span>body</span>
      </Drawer>
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})
