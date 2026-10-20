import React from 'react'
import {
  ArrowRight,
  Code,
  Cpu,
  FileSearch,
  GitBranch,
  GitPullRequest,
  ShieldCheck,
  Terminal,
} from 'lucide-react'
import { StatusBadge } from './StatusBadge'

export interface StageNode {
  id: string
  label: string
  icon: React.ComponentType<{ size?: number; className?: string }>
  evidenceKey?: string
  evidenceValue?: string | null
}

interface PipelineStageGraphProps {
  currentStage?: string | null
  pipelineStatus?: string | null
  approvalStatus?: string | null
  prUrl?: string | null
  prNumber?: number | null
  diagnosisId?: string | null
  proposalId?: string | null
  approvalId?: string | null
  mutationId?: string | null
  validationStatus?: string | null
  failureReason?: string | null
  selectedStageId?: string | null
  onStageSelect?: (stageId: string) => void
  showStatusBanner?: boolean
}

const CANONICAL_STAGES: StageNode[] = [
  { id: 'ingestion', label: 'GitHub Ingestion', icon: GitBranch },
  { id: 'diagnosis', label: 'Diagnosis', icon: Cpu },
  { id: 'context', label: 'Context Retrieval', icon: FileSearch },
  { id: 'proposal', label: 'Fix Proposal', icon: Code },
  { id: 'validation', label: 'Sandbox Validation', icon: Terminal },
  { id: 'approval', label: 'Human Approval', icon: ShieldCheck },
  { id: 'mutation', label: 'Mutation', icon: ArrowRight },
  { id: 'pull_request', label: 'Pull Request', icon: GitPullRequest },
]

export const PipelineStageGraph: React.FC<PipelineStageGraphProps> = ({
  currentStage,
  pipelineStatus,
  approvalStatus,
  prUrl,
  prNumber,
  diagnosisId,
  proposalId,
  approvalId,
  mutationId,
  validationStatus,
  failureReason,
  selectedStageId,
  onStageSelect,
  showStatusBanner = false,
}) => {
  const overallStatus = pipelineStatus || currentStage || null

  // Map factual evidence to stages
  const getStageEvidence = (stageId: string): string | null => {
    switch (stageId) {
      case 'diagnosis':
        return diagnosisId ? `ID: ${diagnosisId.slice(0, 8)}` : null
      case 'proposal':
        return proposalId ? `ID: ${proposalId.slice(0, 8)}` : null
      case 'validation':
        return validationStatus ? `Status: ${validationStatus.toUpperCase()}` : null
      case 'approval':
        return approvalStatus
          ? `Gate: ${approvalStatus.toUpperCase()}`
          : approvalId
          ? `ID: ${approvalId.slice(0, 8)}`
          : null
      case 'mutation':
        return mutationId ? `ID: ${mutationId.slice(0, 8)}` : null
      case 'pull_request':
        return prNumber ? `PR #${prNumber}` : prUrl ? 'PR Created' : null
      default:
        return null
    }
  }

  return (
    <div className="pipeline-stage-graph-container" aria-label="Pipeline Stage Visualization">
      {showStatusBanner && overallStatus && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 8,
            padding: '4px 8px',
            backgroundColor: 'var(--bg-app)',
            borderRadius: 2,
            border: '1px solid var(--border-subtle)',
            fontSize: '0.72rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--text-muted)' }}>Persisted Pipeline Status:</span>
            <StatusBadge status={overallStatus} type="pipeline" />
          </div>
          {failureReason && (
            <span
              className="mono"
              style={{ color: 'var(--status-danger)', fontSize: '0.68rem', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={failureReason}
            >
              Halted: {failureReason}
            </span>
          )}
        </div>
      )}

      {/* Neutral Topology Diagram */}
      <div className="pipeline-stage-graph">
        {CANONICAL_STAGES.map((stage, idx) => {
          const Icon = stage.icon
          const isSelected = selectedStageId === stage.id
          const evidence = getStageEvidence(stage.id)

          return (
            <React.Fragment key={stage.id}>
              <div
                className={`stage-node stage-node-neutral ${isSelected ? 'stage-node-selected' : ''}`}
                onClick={() => onStageSelect && onStageSelect(stage.id)}
                role={onStageSelect ? 'button' : 'region'}
                tabIndex={onStageSelect ? 0 : undefined}
                title={`Stage 0${idx + 1}: ${stage.label}${evidence ? ` (${evidence})` : ''}`}
              >
                <div className="stage-node-header">
                  <span className="stage-node-num">0{idx + 1}</span>
                  {evidence && (
                    <span className="stage-node-evidence-dot" title={evidence} />
                  )}
                </div>

                <div className="stage-node-body">
                  <Icon size={13} className="stage-node-main-icon" />
                  <div className="stage-node-title">{stage.label}</div>
                </div>

                <div className="stage-node-badge">
                  {evidence ? (
                    <span style={{ color: 'var(--text-secondary)' }}>{evidence}</span>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>TOPOLOGY</span>
                  )}
                </div>
              </div>

              {idx < CANONICAL_STAGES.length - 1 && (
                <div className="stage-connector">
                  <span className="stage-connector-line" />
                </div>
              )}
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}
