import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PipelineExplorer } from '../components/Pipelines/PipelineExplorer'
import { apiClient } from '../services/api'
import { mockPipelineSummaryList } from './fixtures'

describe('PipelineExplorer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders pipeline explorer without depending on internal JSON blobs', async () => {
    const pipeSpy = vi
      .spyOn(apiClient, 'getPipelines')
      .mockResolvedValue(mockPipelineSummaryList)

    render(
      <MemoryRouter>
        <PipelineExplorer />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Pipeline Audit Ledger')).toBeInTheDocument()
    })

    expect(pipeSpy).toHaveBeenCalledWith({ limit: 20, offset: 0 })

    // Verify pipeline IDs, incident links, repos rendered
    expect(screen.getAllByText('pipe_test_01').length).toBeGreaterThan(0)
    expect(screen.getByText('inc_test_01_')).toBeInTheDocument()
    expect(screen.getAllByText('crlabs/backend-service').length).toBeGreaterThan(0)
    expect(screen.getByText('crlabs/data-pipeline')).toBeInTheDocument()

    // Verify stage graph rendered for selected pipeline
    expect(screen.getByLabelText('Pipeline Stage Visualization')).toBeInTheDocument()
  })

  it('renders empty state when no pipelines exist', async () => {
    vi.spyOn(apiClient, 'getPipelines').mockResolvedValue([])

    render(
      <MemoryRouter>
        <PipelineExplorer />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('No pipeline records match filter')).toBeInTheDocument()
    })
  })
})
