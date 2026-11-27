import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import LandingPage from '../site/LandingPage'
import { CONSOLE_PATH, REPOSITORY_URL, WORKFLOW } from '../site/content'

function renderLanding() {
  return render(
    <MemoryRouter>
      <LandingPage />
    </MemoryRouter>,
  )
}

describe('landing page', () => {
  it('has one h1 with the product promise, and every section is a labelled landmark', () => {
    renderLanding()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Broken builds, fixed with proof.')
    for (const name of [
      'A red build stops more than the pipeline.',
      /From failed run to reviewed fix/,
      'Built for the work between failure and fix.',
      'Trust is designed in, not bolted on.',
      'One incident, every step in view.',
      'Fits the tools your team already uses.',
      /Keep shipping/,
    ]) {
      expect(screen.getByRole('region', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
  })

  it('tells the workflow in order, all seven stages, as real text', () => {
    renderLanding()
    const workflow = screen.getByRole('region', { name: /From failed run to reviewed fix/ })
    const titles = within(workflow).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(titles).toEqual(WORKFLOW.map((stage) => stage.title))
    expect(titles).toEqual([
      'Failure detected',
      'Verified evidence',
      'AI diagnosis',
      'Fix proposal',
      'Isolated validation',
      'Human approval',
      'Pull request',
    ])
  })

  it('only offers calls to action that lead somewhere real', () => {
    renderLanding()
    const links = screen.getAllByRole('link')
    const console = links.filter((link) => /console/i.test(link.textContent ?? ''))
    expect(console.length).toBeGreaterThan(0)
    console.forEach((link) => expect(link).toHaveAttribute('href', CONSOLE_PATH))

    links
      .filter((link) => link.getAttribute('href')?.startsWith('http'))
      .forEach((link) => {
        expect(link).toHaveAttribute('href', REPOSITORY_URL)
        expect(link).toHaveAttribute('rel', 'noopener noreferrer')
      })

    // No sign-up, waitlist or pricing promises until a backend exists for them.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/sign up|waitlist|pricing|free trial/i)
  })

  it('is honest about the preview: sample data is labelled and the remediation hold is disclosed', () => {
    renderLanding()
    expect(screen.getByRole('figure', { name: /Illustrative demo.*sample data/ })).toBeInTheDocument()
    expect(screen.getByText('Illustrative demo · sample data')).toBeInTheDocument()
    expect(screen.getAllByText(/Private preview/).length).toBeGreaterThan(0)
    expect(screen.getByText(/holds remediation until a repository’s provenance is independently verified/)).toBeInTheDocument()
  })

  it('never claims automatic merging; it only ever says Akesis does not merge', () => {
    renderLanding()
    // Check each text node on its own: textContent would glue neighbouring elements together.
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    const mentions: string[] = []
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const value = node.textContent ?? ''
      if (/merg/i.test(value)) mentions.push(value)
    }
    expect(mentions.length).toBeGreaterThan(0)
    for (const phrase of mentions) {
      expect(phrase).toMatch(/\b(no|never|not|nothing|left to|stays with)\b/i)
    }
    expect(screen.getByText('No automatic merges. Ever.')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/autonomous/i)
  })

  it('states the supported scope: Python on GitHub Actions', () => {
    renderLanding()
    expect(screen.getAllByText(/Python/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/GitHub Actions/).length).toBeGreaterThan(0)
  })

  it('opens and closes the mobile menu with a properly wired toggle', () => {
    renderLanding()
    const toggle = screen.getByRole('button', { name: 'Open menu' })
    const menu = document.getElementById(toggle.getAttribute('aria-controls')!)!
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(menu).not.toBeVisible()

    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Close menu' })).toHaveAttribute('aria-expanded', 'true')
    expect(menu).toBeVisible()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Open menu' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('keeps decorative art out of the accessibility tree', () => {
    const { container } = renderLanding()
    const graph = container.querySelector('[data-hero-mark]')!
    expect(graph).toHaveAttribute('aria-hidden', 'true')
  })
})
