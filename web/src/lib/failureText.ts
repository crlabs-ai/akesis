/**
 * Human-readable meaning for the failure codes the backend stores. Codes are shown beside the
 * description so the technical value is never hidden. Unknown codes are shown as they are.
 */
const DESCRIPTIONS: Record<string, string> = {
  github_rate_limited: 'GitHub rate limit reached',
  github_api_error: 'GitHub API returned an error',
  github_network_error: 'Could not reach GitHub',
  github_app_http_error: 'GitHub App request failed',
  installation_suspended: 'The GitHub App installation is suspended',
  invalid_installation_token: 'GitHub issued an invalid installation token',
  app_not_configured: 'GitHub App credentials are not configured',
  identity_incomplete: 'Stored delivery identity is incomplete',
  verification_retries_exhausted: 'All verification attempts were used',
  verification_deadline_exceeded: 'Verification took longer than the deadline',
  provenance_mismatch: 'Verified provenance did not match the event',
  semantic_identity_conflict: 'Conflicts with an earlier delivery for the same run',
}

export const describeFailureCode = (code: string | null): string | null => {
  if (!code) return null
  return DESCRIPTIONS[code] ?? null
}
