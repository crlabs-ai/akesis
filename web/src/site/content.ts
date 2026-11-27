/**
 * Every product claim on the public site lives here, so it can be reviewed in one place and
 * tested. Rule: describe only what is implemented on main. Scope is Python repositories on GitHub
 * Actions (docs/01-product/scope.md). During private preview the remediation hold (ADR-0010)
 * stops remediation until provenance is independently verified, and the site says so.
 */

export const REPOSITORY_URL = 'https://github.com/crlabs-ai/akesis'
export const CONSOLE_PATH = '/app'

export const HERO = {
  badge: 'Self-healing CI/CD · Private preview',
  titleLead: 'Broken builds,',
  titleAccent: 'fixed with proof.',
  lead:
    'Akesis investigates failed GitHub Actions runs, proves a minimal fix in an isolated sandbox, ' +
    'and opens a pull request only after your team approves it.',
  assurance: 'No automatic merges. Ever.',
}

export const PROBLEMS = [
  {
    title: 'Logs before answers',
    body: 'A failed run means scrolling through runner output to find the one line that matters, then working out whether it is your code, a dependency or the pipeline itself.',
  },
  {
    title: 'Flow, interrupted',
    body: 'Someone stops feature work, reproduces the failure locally and guesses at a fix. Lint errors, broken imports and failing assertions cost the same context switch every time.',
  },
  {
    title: 'Fixes nobody verified',
    body: 'Automation that pushes changes without proof just moves the risk. A fix is only useful if it is small, tested and reviewed by someone accountable.',
  },
] as const

export interface WorkflowStage {
  id: string
  step: string
  title: string
  body: string
  tags: string[]
}

export const WORKFLOW: WorkflowStage[] = [
  {
    id: 'failure',
    step: '01',
    title: 'Failure detected',
    body: 'A failed workflow run reaches Akesis as a GitHub webhook. Its HMAC-SHA256 signature is checked before anything in it is trusted.',
    tags: ['GitHub webhook', 'HMAC-SHA256'],
  },
  {
    id: 'evidence',
    step: '02',
    title: 'Verified evidence',
    body: 'The run is re-confirmed against the GitHub API through the Akesis GitHub App. Workflow logs are retrieved with secrets redacted before analysis.',
    tags: ['GitHub App', 'Redacted logs'],
  },
  {
    id: 'diagnosis',
    step: '03',
    title: 'AI diagnosis',
    body: 'Gemini diagnoses the failure from the verified log signal and repository context. Its answer is structured and validated before Akesis acts on it.',
    tags: ['Structured output', 'Validated'],
  },
  {
    id: 'proposal',
    step: '04',
    title: 'Fix proposal',
    body: 'Akesis proposes a minimal unified diff for lint, import and dependency, or pytest failures in Python projects.',
    tags: ['Unified diff', 'Minimal change'],
  },
  {
    id: 'validation',
    step: '05',
    title: 'Isolated validation',
    body: 'The patch is applied to a private snapshot and checked with pytest, ruff, mypy or compileall in a container with no network, a read-only root and no capabilities, through a signed executor.',
    tags: ['--network none', 'Signed executor'],
  },
  {
    id: 'approval',
    step: '06',
    title: 'Human approval',
    body: 'An authorized reviewer approves or rejects the fix in Slack. Nothing moves forward without that recorded decision.',
    tags: ['Slack', 'Authorized reviewers'],
  },
  {
    id: 'pull-request',
    step: '07',
    title: 'Pull request',
    body: 'Akesis opens a pull request on a fresh branch for your team to review. It never merges anything itself.',
    tags: ['Fresh branch', 'No auto-merge'],
  },
]

export const PREVIEW_NOTE =
  'Private preview: Akesis holds remediation until a repository’s provenance is independently verified, so this pipeline runs end to end only for verified repositories.'

export const CAPABILITIES = [
  {
    title: 'Evidence-first triage',
    body: 'Every incident starts from a verified webhook and a re-confirmed workflow run, with the failing log signal extracted and redacted.',
  },
  {
    title: 'Structured AI diagnosis',
    body: 'Model output is parsed into a typed diagnosis and validated by the application before it can drive anything.',
  },
  {
    title: 'Small, reviewable diffs',
    body: 'Proposals are unified diffs applied verbatim, scoped to the failure, easy to read in one sitting.',
  },
  {
    title: 'Sandboxed validation',
    body: 'Allowlisted checks run in throwaway containers through a separate, signed executor process. Your code never runs with network access.',
  },
  {
    title: 'Durable approvals',
    body: 'Decisions are recorded in PostgreSQL. Repeated Slack deliveries are idempotent and the approver is re-checked at execution time.',
  },
  {
    title: 'Operator console',
    body: 'Incidents, pipelines, webhook deliveries and system health in one place, read from the live API.',
  },
] as const

export const PRINCIPLES = [
  {
    title: 'Provenance first',
    body: 'Nothing is trusted because it arrived. Signatures are verified and runs are re-confirmed with GitHub before any analysis.',
  },
  {
    title: 'Isolated by default',
    body: 'Validation runs as an unprivileged user in a container with no network, a read-only root filesystem and bounded CPU, memory and processes.',
  },
  {
    title: 'People decide',
    body: 'An authorized human approves every change. Approval is checked again when the work runs, not just when the button is pressed.',
  },
  {
    title: 'Nothing merges itself',
    body: 'Akesis opens pull requests. Merging stays with your team and your branch protection.',
  },
] as const

/**
 * The real sandbox flags (src/packages/sdk/sandbox_runner.py), abridged: the workspace mount and
 * container name are left out. Shown in the security section labelled as abridged.
 */
export const SANDBOX_COMMAND = [
  '$ docker create --rm --network none \\',
  '    --cap-drop ALL --security-opt no-new-privileges \\',
  '    --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=64m,mode=1777 \\',
  '    --user <workspace-owner> --pids-limit 256 \\',
  '    --memory 512m --cpus=1.0 akesis-validator:v1 pytest -v',
].join('\n')

export const INTEGRATIONS = [
  {
    name: 'GitHub',
    status: 'Available',
    body: 'A GitHub App receives workflow webhooks, re-confirms runs, reads logs and opens pull requests, using short-lived installation tokens.',
  },
  {
    name: 'Slack',
    status: 'Available',
    body: 'Interactive approval cards for each proposed fix. Every interaction is signature-verified and the approver is checked against an allowlist.',
  },
] as const

export const NAV_LINKS = [
  { href: '#workflow', label: 'Workflow' },
  { href: '#capabilities', label: 'Capabilities' },
  { href: '#security', label: 'Security' },
  { href: '#preview', label: 'Preview' },
] as const

/**
 * Feature spotlights: one idea each, with an illustrative wireframe beside it. The art is
 * decorative and abstract; every claim here describes what main implements today.
 */
export const SPOTLIGHTS = [
  {
    id: 'evidence',
    title: 'Evidence before answers',
    body: 'Every incident starts from a verified webhook and a run re-confirmed with GitHub. Akesis isolates the failing step and redacts secrets before anything is analysed.',
    cta: { label: 'See the workflow', to: '#workflow' },
  },
  {
    id: 'diff',
    title: 'A minimal fix, nothing more',
    body: 'Proposals are small unified diffs scoped to the failure: a lint error, a broken import, a failing assertion. Easy to review in one sitting.',
    cta: { label: 'View a sample', to: '#preview' },
  },
  {
    id: 'sandbox',
    title: 'Proven in isolation',
    body: 'Each patch runs in a throwaway container with no network, a read-only root and no capabilities, through a separate, signed executor.',
    cta: { label: 'Read the security model', to: '#security' },
  },
  {
    id: 'approval',
    title: 'People make the call',
    body: 'An authorized reviewer approves or rejects every fix in Slack. Akesis then opens the pull request, and merging stays with your team.',
    cta: { label: 'Open the console', to: CONSOLE_PATH },
  },
] as const
