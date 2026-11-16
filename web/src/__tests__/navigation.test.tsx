import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CommandPalette } from '../components/Command/CommandPalette'
import { NavRail } from '../components/Layout/NavRail'
import { Header } from '../components/Layout/Header'
import { ALL_ROUTES, ROUTE_GROUPS } from '../lib/routes'
import { openCommandPalette, PALETTE_OPEN_EVENT } from '../lib/paletteEvents'

const Where: React.FC = () => <div data-testid="where">{useLocation().pathname}</div>

const renderShell = (path = '/') =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <NavRail />
      <Header />
      <Where />
      <Routes>
        <Route path="*" element={<div>page</div>} />
      </Routes>
      <CommandPalette />
    </MemoryRouter>
  )

beforeEach(() => {
  window.localStorage.setItem('akesis.sidebar.expanded', 'true')
  if (typeof window.matchMedia !== 'function') {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    })
  }
})

afterEach(() => {
  window.localStorage.clear()
})

describe('grouped navigation', () => {
  it('labels each group in the expanded rail and keeps every route reachable', () => {
    renderShell()
    const primary = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(primary).getByText('Operate')).toBeInTheDocument()
    expect(within(primary).getByText('Delivery & health')).toBeInTheDocument()
    for (const route of ALL_ROUTES) {
      expect(within(primary).getByRole('link', { name: route.label })).toHaveAttribute('href', route.to)
    }
  })

  it('places each route under the group it belongs to', () => {
    renderShell()
    for (const group of ROUTE_GROUPS) {
      const region = screen.getByRole('group', { name: group.label })
      for (const route of group.routes) {
        expect(within(region).getByRole('link', { name: route.label })).toBeInTheDocument()
      }
    }
  })
})

describe('command palette', () => {
  it('opens with Ctrl+K and lists only real routes', () => {
    renderShell()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const list = screen.getByRole('listbox', { name: 'Pages' })
    const options = within(list).getAllByRole('option')
    expect(options.map((o) => o.querySelector('.palette-option-label')?.textContent)).toEqual(
      ALL_ROUTES.map((r) => r.label)
    )
  })

  it('opens with Meta+K as well', () => {
    renderShell()
    fireEvent.keyDown(document, { key: 'K', metaKey: true })
    expect(screen.getByRole('dialog', { name: 'Jump to a page' })).toBeInTheDocument()
  })

  it('filters by label, group and keyword', () => {
    renderShell()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'events' } })
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([expect.stringContaining('Webhook deliveries')])
    fireEvent.change(input, { target: { value: 'nothing-matches-this' } })
    expect(screen.getByRole('status')).toHaveTextContent('No page matches')
  })

  it('moves the active option with arrow keys, wrapping at both ends', () => {
    renderShell()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const input = screen.getByRole('combobox')
    const count = ALL_ROUTES.length
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(screen.getAllByRole('option')[count - 1]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('navigates to the chosen route on Enter and closes', () => {
    renderShell('/')
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'health' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('where')).toHaveTextContent('/health')
    expect(screen.queryByRole('dialog', { name: 'Jump to a page' })).not.toBeInTheDocument()
  })

  it('navigates when an option is clicked', () => {
    renderShell('/')
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    fireEvent.click(screen.getByRole('option', { name: /Incidents/ }))
    expect(screen.getByTestId('where')).toHaveTextContent('/incidents')
  })

  it('closes on Escape without navigating', () => {
    renderShell('/pipelines')
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Jump to a page' })).not.toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/pipelines')
  })

  it('opens from the header button and from the window event', () => {
    renderShell()
    fireEvent.click(screen.getByRole('button', { name: 'Jump to a page (command palette)' }))
    expect(screen.getByRole('dialog', { name: 'Jump to a page' })).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    act(() => {
      openCommandPalette()
    })
    expect(screen.getByRole('dialog', { name: 'Jump to a page' })).toBeInTheDocument()
    expect(PALETTE_OPEN_EVENT).toBe('akesis:open-palette')
  })

  it('returns focus to the element that was focused before it opened', () => {
    renderShell()
    const trigger = screen.getByRole('button', { name: 'Jump to a page (command palette)' })
    trigger.focus()
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(document.activeElement).toBe(trigger)
  })
})
