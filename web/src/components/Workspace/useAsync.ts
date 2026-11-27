import { useCallback, useEffect, useState } from 'react'
import { useWorkspaceReload } from '../../hooks/useWorkspaceReload'

/** Load data for the selected workspace, reload on workspace change, expose errors as values. */
export function useWorkspaceData<T>(load: () => Promise<T>): {
  data: T | null
  error: unknown
  loading: boolean
  reload: () => void
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const run = useCallback(() => {
    setLoading(true)
    load()
      .then((value) => {
        setData(value)
        setError(null)
      })
      .catch((reason: unknown) => setError(reason))
      .finally(() => setLoading(false))
    // `load` is a stable module function in every caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(run, [run])
  useWorkspaceReload(run)
  return { data, error, loading, reload: run }
}

/** Error text for a failed action: backend codes become readable, raw detail never leaks. */
export function actionMessage(error: unknown): string {
  const code = error instanceof Error ? error.message : ''
  const known: Record<string, string> = {
    insufficient_role: 'Your role does not allow this.',
    cannot_change_own_role: 'You cannot change your own role.',
    cannot_map_own_slack_identity: 'Your own Slack identity cannot be mapped to another member.',
    owner_required: 'Only a workspace owner can do this.',
    member_cannot_approve: 'Only approvers, maintainers, admins and owners can be mapped to Slack.',
    last_owner: 'A workspace needs at least one owner.',
    use_leave: 'Use “Leave workspace” to remove yourself.',
    personal_workspace: 'A personal workspace has exactly one member.',
    already_member: 'That GitHub user is already a member.',
    invitation_pending: 'An invitation for that GitHub user is already pending.',
    invalid_github_login: 'That is not a valid GitHub username.',
    github_user_not_found: 'GitHub has no user with that username.',
    github_account_not_a_user: 'That is an organization or app account, not a person.',
    installation_account_not_yours:
      'That installation is on another GitHub account. Only its owner can link it.',
    org_owner_required:
      'Only a verified owner of that GitHub organization can link its installation. (The Akesis App also needs the organization “Members: read” permission to verify this.)',
    installation_account_unsupported: 'Installations on that kind of GitHub account are not supported.',
    installation_has_no_repositories: 'That installation has no repositories selected on GitHub.',
    installation_ownership_mismatch: 'GitHub reported repositories from another account. Linking was refused.',
    installation_not_verified: 'GitHub did not confirm that installation for the Akesis App.',
    invalid_slack_user_id: 'Slack user IDs look like U0123ABCD.',
    slack_user_already_mapped: 'That Slack user is already mapped to another member.',
    installation_not_accessible: 'Your GitHub account cannot access that installation.',
    installation_already_linked: 'That installation is linked to another workspace.',
    installation_suspended: 'That installation is suspended on GitHub.',
    personal_workspace_own_account_only:
      'A personal workspace can link only the installation on your own GitHub account. Use a team workspace for an organization.',
    github_reauthorization_required: 'GitHub access expired. Sign out and sign in again.',
    token_encryption_not_configured: 'The server is missing AUTH_TOKEN_ENCRYPTION_KEY.',
    github_app_not_configured: 'The server is missing the GitHub App ID or private key.',
    github_unavailable: 'GitHub could not be reached. Try again.',
    invalid_workspace_name: 'Names are 2 to 100 characters.',
  }
  return known[code] ?? (code && /^[a-z_]+$/.test(code) ? code.replace(/_/g, ' ') : 'The request failed.')
}
