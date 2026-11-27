/**
 * The operator console, mounted under /app and shown only to a signed-in user. Every view is
 * scoped to the selected workspace. Its legacy stylesheet is
 * imported only here, so it loads with this lazily loaded chunk and never with the public site.
 */
import React, { useEffect, useState, useSyncExternalStore } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import '../index.css'
import { NavRail } from '../components/Layout/NavRail'
import { Header } from '../components/Layout/Header'
import { OverviewView } from '../components/Overview/OverviewView'
import { IncidentsList } from '../components/Incidents/IncidentsList'
import { IncidentDetailWorkspace } from '../components/IncidentDetail/IncidentDetailWorkspace'
import { PipelineExplorer } from '../components/Pipelines/PipelineExplorer'
import { WebhookDeliveriesView } from '../components/Deliveries/WebhookDeliveriesView'
import { SystemHealthView } from '../components/Health/SystemHealthView'
import { PageTransition } from '../components/Layout/PageTransition'
import { CommandPalette } from '../components/Command/CommandPalette'
import { MembersView } from '../components/Workspace/MembersView'
import { SettingsView } from '../components/Workspace/SettingsView'
import { OnboardingView } from '../components/Workspace/OnboardingView'
import { ApprovalsView } from '../components/Workspace/ApprovalsView'
import { APP_BASE } from '../lib/routes'
import { refreshSession } from '../services/api'
import { session } from '../services/session'

/**
 * Signed-in gate for the console. It is a convenience, not a control: every API route enforces
 * the session and workspace membership itself, so a bypassed gate shows nothing.
 */
const RequireSession: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const state = useSyncExternalStore(session.subscribe, session.getState, session.getState)
  const location = useLocation()
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (state.status === 'loading') refreshSession().then((ok) => ok || session.signedOut(null), () => setFailed(true))
  }, [state.status])

  if (failed) {
    return (
      <div className="state-box state-box-error" role="alert">
        <p className="state-box-title">Backend unreachable</p>
        <p className="state-box-desc">The console could not check your session. Refresh to try again.</p>
      </div>
    )
  }
  if (state.status === 'signed_out') {
    const back = encodeURIComponent(location.pathname + location.search)
    const reason = state.reason ? `&error=${encodeURIComponent(state.reason)}` : ''
    return <Navigate to={`/login?return_to=${back}${reason}`} replace />
  }
  if (state.status === 'loading' || !state.workspace) {
    return <div className="content-area" aria-busy="true" aria-label="Checking your session" />
  }
  return <>{children}</>
}

export const Console: React.FC = () => {
  const [refreshKey, setRefreshKey] = useState<number>(0)
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false)

  useEffect(() => {
    document.title = 'Akesis · Console'
  }, [])

  const handleManualRefresh = () => {
    setIsRefreshing(true)
    setRefreshKey((prev) => prev + 1)
    setTimeout(() => setIsRefreshing(false), 500)
  }

  return (
    <RequireSession>
      <div className="app-shell">
        <NavRail />
        <div className="main-wrapper">
          <Header onRefresh={handleManualRefresh} isRefreshing={isRefreshing} />
          <main className="content-area" key={refreshKey}>
            <PageTransition>
              <Routes>
                <Route index element={<OverviewView />} />
                <Route path="incidents" element={<IncidentsList />} />
                <Route path="incidents/:incidentId" element={<IncidentDetailWorkspace />} />
                <Route path="pipelines" element={<PipelineExplorer />} />
                <Route path="deliveries" element={<WebhookDeliveriesView />} />
                <Route path="approvals" element={<ApprovalsView />} />
                <Route path="health" element={<SystemHealthView />} />
                <Route path="onboarding" element={<OnboardingView />} />
                <Route path="members" element={<MembersView />} />
                <Route path="settings" element={<SettingsView />} />
                <Route path="*" element={<Navigate to={APP_BASE} replace />} />
              </Routes>
            </PageTransition>
          </main>
        </div>
      </div>
      <CommandPalette />
    </RequireSession>
  )
}

export default Console
