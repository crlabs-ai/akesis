import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { IncidentDetailWorkspace } from '../components/IncidentDetail/IncidentDetailWorkspace'
import { UnifiedDiffViewer } from '../components/IncidentDetail/UnifiedDiffViewer'
import { apiClient } from '../services/api'
import type { IncidentDetail } from '../types/api'
import { mockIncidentDetail } from './fixtures'

// Test-only long-content fixtures. Nothing here is written to runtime data or the database.
const LONG_ID = `inc_${'a1b2c3d4e5f6'.repeat(12)}`
const LONG_APPROVAL_ID = `appr_prop_${'f0e1d2c3b4a5'.repeat(10)}`
const LAST_STDOUT_LINE = 'FINAL_STDOUT_LINE_SENTINEL'
const LAST_STDERR_LINE = 'FINAL_STDERR_LINE_SENTINEL'
const LAST_LOG_LINE = 'FINAL_LOG_LINE_SENTINEL'

const manyLines = (count: number, prefix: string) =>
  Array.from({ length: count }, (_, i) => `${prefix} line ${i + 1}`).join('\n')

const longDiff = [
  '--- a/src/very/deep/module/path/that/keeps/going/service.py',
  '+++ b/src/very/deep/module/path/that/keeps/going/service.py',
  '@@ -1,400 +1,400 @@',
  ...Array.from({ length: 400 }, (_, i) => (i % 2 === 0 ? `-old_value_${i} = ${'x'.repeat(120)}` : `+new_value_${i} = ${'y'.repeat(120)}`)),
  `+${LAST_LOG_LINE}`,
].join('\n')

const buildLongIncident = (): IncidentDetail => ({
  ...mockIncidentDetail,
  incident_id: LONG_ID,
  approval_id: LONG_APPROVAL_ID,
  diff_content: longDiff,
  traceback_excerpt: `${manyLines(300, 'log')}\n${LAST_LOG_LINE}`,
  failure_context: {
    ...mockIncidentDetail.failure_context!,
    incident_id: LONG_ID,
    raw_log_excerpt: `${manyLines(300, 'log')}\n${LAST_LOG_LINE}`,
  },
  sandbox_stdout: `${manyLines(500, 'stdout')}\n${LAST_STDOUT_LINE}`,
  sandbox_stderr: `${manyLines(500, 'stderr')}\n${LAST_STDERR_LINE}`,
  validation_result: {
    ...mockIncidentDetail.validation_result!,
    stdout: `${manyLines(500, 'stdout')}\n${LAST_STDOUT_LINE}`,
    stderr: `${manyLines(500, 'stderr')}\n${LAST_STDERR_LINE}`,
  },
})

const renderWorkspace = (incidentId: string) =>
  render(
    <MemoryRouter initialEntries={[`/incidents/${incidentId}`]}>
      <Routes>
        <Route path="/incidents/:incidentId" element={<IncidentDetailWorkspace />} />
      </Routes>
    </MemoryRouter>
  )

describe('UnifiedDiffViewer long content', () => {
  it('keeps every diff line inside the single scroll region', () => {
    render(<UnifiedDiffViewer diff={longDiff} filePath="src/service.py" />)

    const region = screen.getByLabelText('Unified Diff Viewer')
    const scrollBody = region.querySelector('.diff-body')
    expect(scrollBody).not.toBeNull()

    // The scroll region is the only container for the line list, so overflow stays inside it.
    const lines = scrollBody!.querySelectorAll('.diff-line')
    expect(lines.length).toBe(longDiff.split('\n').length)
    expect(within(scrollBody as HTMLElement).getByText(`+${LAST_LOG_LINE}`)).toBeInTheDocument()
  })

  it('renders long unbroken file paths in the header without truncating them', () => {
    const path = 'src/' + 'segment_'.repeat(40) + 'service.py'
    render(<UnifiedDiffViewer diff={longDiff} filePath={path} />)

    expect(screen.getByText(path)).toBeInTheDocument()
  })

  it('copies the complete diff, not just the visible portion', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    render(<UnifiedDiffViewer diff={longDiff} filePath="src/service.py" />)
    fireEvent.click(screen.getByRole('button', { name: 'Copy Patch Diff' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(longDiff))
  })
})

describe('IncidentDetailWorkspace long content', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders long incident and approval IDs in full', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(buildLongIncident())
    renderWorkspace(LONG_ID)

    await waitFor(() => expect(screen.getByText(LONG_ID)).toBeInTheDocument())
    expect(screen.getByText(LONG_APPROVAL_ID)).toBeInTheDocument()
  })

  it('keeps the full failure log excerpt inside its own container and copies all of it', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(buildLongIncident())
    renderWorkspace(LONG_ID)

    await waitFor(() => expect(screen.getByText('Cleaned CI Log Excerpt')).toBeInTheDocument())

    const leftColumn = document.querySelector('.workstation-left') as HTMLElement
    const logBlock = leftColumn.querySelector('pre.ws-log-body') as HTMLElement
    expect(logBlock.textContent).toContain(LAST_LOG_LINE)

    const logCopy = within(leftColumn).getByRole('button', { name: 'Copy Log excerpt' })
    fireEvent.click(logCopy)
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining(LAST_LOG_LINE))
    )
  })

  it('keeps the full stdout and stderr inside the terminal and switches tabs without losing them', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(buildLongIncident())
    renderWorkspace(LONG_ID)

    await waitFor(() => expect(screen.getByLabelText('Docked Terminal Console')).toBeInTheDocument())
    const terminal = screen.getByLabelText('Docked Terminal Console')

    fireEvent.click(within(terminal).getByRole('button', { name: /stdout/i }))
    const stdoutBlock = terminal.querySelector('pre.terminal-view') as HTMLElement
    expect(stdoutBlock.textContent).toContain(LAST_STDOUT_LINE)
    expect(within(terminal).getAllByText('(501L)').length).toBeGreaterThan(0)

    fireEvent.click(within(terminal).getByRole('button', { name: /stderr/i }))
    const stderrBlock = terminal.querySelector('pre.terminal-view-stderr') as HTMLElement
    expect(stderrBlock.textContent).toContain(LAST_STDERR_LINE)
    expect(within(terminal).getAllByText('(501L)').length).toBeGreaterThan(0)
  })

  it('keeps terminal tabs and the collapse control reachable by role and name', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(buildLongIncident())
    renderWorkspace(LONG_ID)

    await waitFor(() => expect(screen.getByLabelText('Docked Terminal Console')).toBeInTheDocument())
    const terminal = screen.getByLabelText('Docked Terminal Console')

    for (const name of [/validation/i, /stdout/i, /stderr/i, /failure evidence/i]) {
      const tab = within(terminal).getByRole('button', { name })
      expect(tab.tagName).toBe('BUTTON')
      expect(tab).not.toBeDisabled()
    }

    const collapse = within(terminal).getByRole('button', { name: /collapse console/i })
    fireEvent.click(collapse)
    expect(within(terminal).getByRole('button', { name: /expand console/i })).toBeInTheDocument()
  })

  it('keeps the existing copy action for the full diff on the detail route', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(buildLongIncident())
    renderWorkspace(LONG_ID)

    await waitFor(() => expect(screen.getByLabelText('Unified Diff Viewer')).toBeInTheDocument())
    const diffRegion = screen.getByLabelText('Unified Diff Viewer')
    fireEvent.click(within(diffRegion).getByRole('button', { name: 'Copy Patch Diff' }))

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(longDiff))
  })
})
