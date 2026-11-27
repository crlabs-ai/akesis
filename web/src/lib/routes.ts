import { Activity, AlertOctagon, GitMerge, LayoutDashboard, Webhook, type LucideIcon } from 'lucide-react'

/** The operator console lives under /app; the public site owns /. */
export const APP_BASE = '/app'

/** A console path ("/incidents") as a routable URL ("/app/incidents"). "/" is the console home. */
export function appPath(path: string): string {
  return path === '/' ? APP_BASE : `${APP_BASE}${path}`
}

/** Paths the console used before it moved under /app; they redirect so old links keep working. */
export const LEGACY_CONSOLE_PATHS = ['/incidents', '/pipelines', '/deliveries', '/health'] as const

export interface AppRoute {
  to: string
  label: string
  icon: LucideIcon
  end: boolean
  /** Extra words the command palette matches on, so "events" finds Deliveries. */
  keywords: string[]
}

export interface RouteGroup {
  label: string
  routes: AppRoute[]
}

/**
 * The only navigable destinations. Every entry is a real route in App.tsx. Add a route here only
 * after it exists there, so neither the rail nor the palette can offer a dead link.
 */
export const ROUTE_GROUPS: RouteGroup[] = [
  {
    label: 'Operate',
    routes: [
      { to: appPath('/'), label: 'Overview', icon: LayoutDashboard, end: true, keywords: ['home', 'command center', 'status'] },
      { to: appPath('/incidents'), label: 'Incidents', icon: AlertOctagon, end: false, keywords: ['failures', 'investigation'] },
      { to: appPath('/pipelines'), label: 'Pipelines', icon: GitMerge, end: false, keywords: ['workflow', 'stages', 'remediation'] },
    ],
  },
  {
    label: 'Delivery & health',
    routes: [
      { to: appPath('/deliveries'), label: 'Webhook deliveries', icon: Webhook, end: false, keywords: ['deliveries', 'webhook', 'retry', 'events'] },
      { to: appPath('/health'), label: 'System Health', icon: Activity, end: false, keywords: ['health', 'liveness', 'readiness', 'worker'] },
    ],
  },
]

export const ALL_ROUTES: AppRoute[] = ROUTE_GROUPS.flatMap((group) => group.routes)
