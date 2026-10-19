/**
 * TypeScript API contracts matching Akesis backend response models.
 */

export interface LivenessResponse {
  status: string
}

export interface ReadinessResponse {
  status: string
  environment?: string
}

export type PipelineStatus =
  | 'received'
  | 'analyzing'
  | 'diagnosed'
  | 'proposing'
  | 'proposed'
  | 'validating'
  | 'validated'
  | 'awaiting_approval'
  | 'approved'
  | 'mutating'
  | 'completed'
  | 'failed'
  | 'rejected'
  | 'quarantined'
  | string

export type ValidationStatus =
  | 'passed'
  | 'failed'
  | 'timed_out'
  | 'patch_rejected'
  | 'unsupported'
  | 'infrastructure_error'
  | string

export interface FailureSignal {
  category: string
  error_type: string
  failure_message: string
  target_file?: string | null
  target_line?: number | null
  extracted_snippet?: string
}

export interface FailureContext {
  incident_id: string
  repository_owner: string
  repository_name: string
  run_id: number
  workflow_name: string
  commit_sha: string
  branch: string
  run_url: string
  conclusion: string
  signal: FailureSignal
  raw_log_excerpt: string
  created_at: string
}

export interface FixProposal {
  proposal_id: string
  incident_id: string
  diagnosis_id?: string | null
  explanation?: string
  rationale?: string
  target_files: string[]
  unified_diff: string
  assumptions: string[]
  risk_assessment?: string
  estimated_risk_level?: 'low' | 'medium' | 'high' | string
  risk_level?: 'low' | 'medium' | 'high' | string
  confidence_score: number
  created_at: string
}

export interface ValidationResult {
  validation_id: string
  proposal_id: string
  incident_id: string
  commit_sha: string
  status: ValidationStatus
  command_executed: string
  exit_code: number | null
  stdout: string
  stderr: string
  duration_ms: number
  timed_out: boolean
  failure_reason?: string | null
  created_at: string
}

export interface IncidentSummary {
  incident_id: string
  repository: string
  run_id: number
  commit_sha: string
  workflow_name: string
  branch: string
  category: string
  error_type: string
  failure_message: string
  current_stage: string
  approval_status: string | null
  approval_id: string | null
  target_file: string | null
  target_line: number | null
  pr_number: number | null
  pr_url: string | null
  created_at: string
  updated_at: string
}

export interface IncidentDetail extends IncidentSummary {
  diff_content: string | null
  failure_context: FailureContext | null
  fix_proposal: FixProposal | null
  validation_result: ValidationResult | null
  traceback_excerpt: string | null
  sandbox_command: string | null
  sandbox_exit_code: number | null
  sandbox_stdout: string | null
  sandbox_stderr: string | null
}

export interface PipelineSummary {
  pipeline_id: string
  incident_id: string
  repository_owner: string
  repository_name: string
  run_id: number
  commit_sha: string
  status: PipelineStatus
  diagnosis_id: string | null
  proposal_id: string | null
  approval_id: string | null
  mutation_id: string | null
  pr_number: number | null
  pr_url: string | null
  failure_reason: string | null
  created_at: string
  updated_at: string
}
