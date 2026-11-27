import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const lenis = vi.hoisted(() => ({
  instances: [] as { destroy: ReturnType<typeof vi.fn>; on: ReturnType<typeof vi.fn>; raf: ReturnType<typeof vi.fn> }[],
}))

vi.mock('lenis', () => ({
  default: vi.fn().mockImplementation(function Lenis() {
    const instance = { destroy: vi.fn(), on: vi.fn(), raf: vi.fn() }
    lenis.instances.push(instance)
    return instance
  }),
}))

import { usePrefersReducedMotion, useRichMotion, useSmoothScroll } from '../design/motion'

function setMedia(matching: (query: string) => boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: matching(query),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    }),
  })
}

let original: typeof window.matchMedia

beforeEach(() => {
  original = window.matchMedia
  lenis.instances.length = 0
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: original })
})

function Probe({ onRender }: { onRender: (state: { reduced: boolean; rich: boolean }) => void }) {
  onRender({ reduced: usePrefersReducedMotion(), rich: useRichMotion() })
  return null
}

describe('motion policy', () => {
  it('treats an explicit reduce preference as reduced motion, with no rich scroll', () => {
    // A browser with "reduce" set answers false to the no-preference query.
    setMedia((query) => query.includes('prefers-reduced-motion: reduce'))
    let state = { reduced: false, rich: true }
    render(<Probe onRender={(s) => (state = s)} />)
    expect(state).toEqual({ reduced: true, rich: false })
  })

  it('treats a missing or unanswerable media query as reduced motion (motion is opt-in)', () => {
    setMedia(() => false)
    let state = { reduced: false, rich: true }
    render(<Probe onRender={(s) => (state = s)} />)
    expect(state).toEqual({ reduced: true, rich: false })
  })

  it('enables rich motion only for no-preference, a fine pointer and a wide screen', () => {
    setMedia(() => true)
    let state = { reduced: true, rich: false }
    render(<Probe onRender={(s) => (state = s)} />)
    expect(state).toEqual({ reduced: false, rich: true })
  })
})

describe('useSmoothScroll', () => {
  function Smooth({ enabled }: { enabled: boolean }) {
    useSmoothScroll(enabled)
    return null
  }

  it('never creates Lenis when disabled', () => {
    render(<Smooth enabled={false} />)
    expect(lenis.instances).toHaveLength(0)
  })

  it('creates one Lenis while enabled and destroys it on unmount', () => {
    const { unmount } = render(<Smooth enabled />)
    expect(lenis.instances).toHaveLength(1)
    expect(document.documentElement).toHaveClass('lenis')
    unmount()
    expect(lenis.instances[0].destroy).toHaveBeenCalledTimes(1)
    expect(document.documentElement).not.toHaveClass('lenis')
  })

  it('tears Lenis down when it becomes disabled (for example, reduced motion switched on)', () => {
    const { rerender } = render(<Smooth enabled />)
    rerender(<Smooth enabled={false} />)
    expect(lenis.instances[0].destroy).toHaveBeenCalledTimes(1)
  })
})
