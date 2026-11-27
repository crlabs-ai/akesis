import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  Badge,
  Button,
  ButtonLink,
  CodeBlock,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  StatusDot,
  Tabs,
  ToastProvider,
  useToast,
} from '../design'

describe('Button', () => {
  it('is disabled and announces busy while loading', () => {
    const onClick = vi.fn()
    render(<Button loading onClick={onClick}>Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    fireEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('defaults to type="button" so it never submits a form by accident', () => {
    render(<Button>Go</Button>)
    expect(screen.getByRole('button', { name: 'Go' })).toHaveAttribute('type', 'button')
  })
})

describe('ButtonLink', () => {
  it('renders in-app routes as router links and external URLs as safe new-tab anchors', () => {
    render(
      <MemoryRouter>
        <ButtonLink to="/app">Console</ButtonLink>
        <ButtonLink to="https://github.com/crlabs-ai/akesis">Source</ButtonLink>
      </MemoryRouter>,
    )
    const internal = screen.getByRole('link', { name: 'Console' })
    expect(internal).toHaveAttribute('href', '/app')
    expect(internal).not.toHaveAttribute('target')
    const external = screen.getByRole('link', { name: 'Source' })
    expect(external).toHaveAttribute('target', '_blank')
    expect(external).toHaveAttribute('rel', 'noopener noreferrer')
  })
})

describe('status', () => {
  it('never relies on colour alone: badges can carry an icon and dots carry a text label', () => {
    const { container } = render(
      <>
        <Badge tone="danger" icon>Failed</Badge>
        <StatusDot tone="success" label="passed" />
      </>,
    )
    expect(screen.getByText('Failed').querySelector('svg')).toBeInTheDocument()
    expect(screen.getByText('passed')).toBeInTheDocument()
    expect(container.querySelector('[aria-hidden="true"]')).toBeInTheDocument()
  })
})

describe('Field', () => {
  it('labels the input and ties hint and error text to it', () => {
    const { rerender } = render(<Field label="Read token" hint="Stored for this tab only" />)
    const input = screen.getByLabelText('Read token')
    expect(input).toHaveAccessibleDescription('Stored for this tab only')
    expect(input).not.toHaveAttribute('aria-invalid')

    rerender(<Field label="Read token" error="Token rejected" />)
    expect(screen.getByLabelText('Read token')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Read token')).toHaveAccessibleDescription('Token rejected')
  })
})

describe('Modal', () => {
  function Harness({ onClose }: { onClose: () => void }) {
    const [open, setOpen] = useState(true)
    return (
      <Modal
        open={open}
        title="Disconnect repository"
        description="This stops new incidents for it."
        onClose={() => {
          onClose()
          setOpen(false)
        }}
      >
        body
      </Modal>
    )
  }

  it('is a labelled dialog that closes from its close button and on Escape (cancel)', () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)
    const dialog = screen.getByRole('dialog', { name: 'Disconnect repository', hidden: true })
    expect(dialog).toHaveAttribute('open')
    expect(dialog).toHaveAccessibleDescription('This stops new incidents for it.')

    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes from the close button', () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog', hidden: true }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('dialog', { hidden: true })).not.toHaveAttribute('open')
  })
})

describe('Toast', () => {
  afterEach(() => vi.useRealTimers())

  function Trigger() {
    const toast = useToast()
    return <Button onClick={() => toast({ title: 'Token saved', tone: 'success', duration: 3000 })}>Notify</Button>
  }

  it('announces through one polite live region and auto-dismisses', () => {
    vi.useFakeTimers()
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    )
    const region = screen.getByRole('list', { name: 'Notifications' })
    expect(region).toHaveAttribute('aria-live', 'polite')

    fireEvent.click(screen.getByRole('button', { name: 'Notify' }))
    expect(region).toHaveTextContent('Token saved')
    act(() => vi.advanceTimersByTime(3000))
    expect(region).not.toHaveTextContent('Token saved')
  })

  it('can be dismissed by the user', () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Notify' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }))
    expect(screen.getByRole('list', { name: 'Notifications' })).not.toHaveTextContent('Token saved')
  })

  it('refuses to be used outside its provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Trigger />)).toThrow(/ToastProvider/)
  })
})

describe('Tabs', () => {
  function Harness() {
    const [value, setValue] = useState('a')
    return (
      <Tabs
        label="Incident"
        value={value}
        onChange={setValue}
        items={[
          { id: 'a', label: 'Diagnosis', panel: 'diagnosis panel' },
          { id: 'b', label: 'Diff', panel: 'diff panel' },
          { id: 'c', label: 'Validation', panel: 'validation panel' },
        ]}
      />
    )
  }

  it('follows the ARIA tabs pattern with roving focus and arrow, Home and End keys', () => {
    render(<Harness />)
    const [first, second, third] = screen.getAllByRole('tab')
    expect(first).toHaveAttribute('aria-selected', 'true')
    expect(first).toHaveAttribute('tabindex', '0')
    expect(second).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('diagnosis panel')

    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(second).toHaveAttribute('aria-selected', 'true')
    expect(second).toHaveFocus()
    expect(screen.getByRole('tabpanel', { name: 'Diff' })).toHaveTextContent('diff panel')

    fireEvent.keyDown(second, { key: 'End' })
    expect(third).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(third, { key: 'ArrowRight' }) // wraps
    expect(first).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(first, { key: 'ArrowLeft' }) // wraps back
    expect(third).toHaveAttribute('aria-selected', 'true')
  })
})

describe('states', () => {
  it('announces errors as alerts; empty states are plain content', () => {
    render(
      <>
        <EmptyState title="No repositories connected">Connect GitHub to begin.</EmptyState>
        <ErrorState title="Could not load incidents">The API did not respond.</ErrorState>
      </>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load incidents')
    expect(screen.getByRole('heading', { name: 'No repositories connected' })).toBeInTheDocument()
  })
})

describe('CodeBlock', () => {
  it('marks diff lines by kind and keeps the text intact', () => {
    render(<CodeBlock diff label="a.py" code={'--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n same'} />)
    const code = screen.getByText('a.py').closest('figure')!
    expect(code.textContent).toContain('-old')
    expect(code.textContent).toContain('+new')
    const added = screen.getByText('+new')
    const removed = screen.getByText('-old')
    expect(added.className).not.toBe(removed.className)
  })
})
