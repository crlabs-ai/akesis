import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { IncidentDetailWorkspace } from '../components/IncidentDetail/IncidentDetailWorkspace'
import { apiClient, ApiError } from '../services/api'
import { mockIncidentDetail } from './fixtures'

describe('IncidentDetailWorkspace', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('requests incident detail on demand and renders full 3-column workstation layout', async () => {
    const detailSpy = vi
      .spyOn(apiClient, 'getIncidentDetail')
      .mockResolvedValue(mockIncidentDetail)

    render(
      <MemoryRouter initialEntries={['/incidents/inc_test_01_lint']}>
        <Routes>
          <Route path="/incidents/:incidentId" element={<IncidentDetailWorkspace />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('inc_test_01_lint')).toBeInTheDocument()
    })

    // Verify detail API endpoint called on demand
    expect(detailSpy).toHaveBeenCalledWith('inc_test_01_lint')

    // Verify pipeline stage graph rendered
    expect(screen.getByLabelText('Pipeline Stage Visualization')).toBeInTheDocument()
    expect(screen.getByText('GitHub Ingestion')).toBeInTheDocument()
    expect(screen.getByText('Diagnosis')).toBeInTheDocument()

    // Left Column: CI Failure Evidence
    expect(screen.getByText('RuffLinterError')).toBeInTheDocument()
    expect(screen.getByText('Cleaned CI Log Excerpt')).toBeInTheDocument()
    expect(screen.getByText('src/main.py:12')).toBeInTheDocument()

    // Center Column: Fix Rationale & Unified Diff Viewer
    expect(
      screen.getByText(
        'Remove unused import `os` from src/main.py to satisfy Ruff linter check.'
      )
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Unified Diff Viewer')).toBeInTheDocument()
    expect(screen.getByText('-import os')).toBeInTheDocument()
    expect(screen.getByText('import sys')).toBeInTheDocument()

    // Right Column: Sandbox Validation Terminal Output
    expect(screen.getByText('uv run ruff check .')).toBeInTheDocument()
    expect(screen.getAllByText('PASSED').length).toBeGreaterThan(0)
  })

  it('supports docked terminal tab switching and collapse toggling', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockResolvedValue(mockIncidentDetail)

    render(
      <MemoryRouter initialEntries={['/incidents/inc_test_01_lint']}>
        <Routes>
          <Route path="/incidents/:incidentId" element={<IncidentDetailWorkspace />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('inc_test_01_lint')).toBeInTheDocument()
    })

    // Docked terminal presence
    expect(screen.getByLabelText('Docked Terminal Console')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /validation/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stdout/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stderr/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /failure evidence/i })).toBeInTheDocument()

    // Switch to Stdout tab
    const stdoutTab = screen.getByRole('button', { name: /stdout/i })
    fireEvent.click(stdoutTab)
    expect(screen.getByText(/All checks passed!/)).toBeInTheDocument()

    // Switch to Failure Evidence tab
    const evidenceTab = screen.getByRole('button', { name: /failure evidence/i })
    fireEvent.click(evidenceTab)
    expect(screen.getAllByText(/F401 `os` imported but unused/).length).toBeGreaterThan(0)

    // Collapse toggle
    const collapseBtn = screen.getByRole('button', { name: /collapse console/i })
    fireEvent.click(collapseBtn)
    // Terminal body is hidden
    expect(screen.queryByText(/All checks passed!/)).not.toBeInTheDocument()

    // Expand again
    const expandBtn = screen.getByRole('button', { name: /expand console/i })
    fireEvent.click(expandBtn)
    expect(screen.getByRole('button', { name: /collapse console/i })).toBeInTheDocument()
  })

  it('handles 404 not found properly', async () => {
    vi.spyOn(apiClient, 'getIncidentDetail').mockRejectedValue(
      new ApiError("Incident 'nonexistent' not found.", 404)
    )

    render(
      <MemoryRouter initialEntries={['/incidents/nonexistent']}>
        <Routes>
          <Route path="/incidents/:incidentId" element={<IncidentDetailWorkspace />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Error Loading Incident Workspace')).toBeInTheDocument()
      expect(screen.getByText("Incident 'nonexistent' not found.")).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /back to incidents/i })).toBeInTheDocument()
    })
  })
})
