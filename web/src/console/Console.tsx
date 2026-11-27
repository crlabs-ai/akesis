/**
 * The operator console, mounted under /app. Phase 1 moves the existing, working console here
 * unchanged; Phase 2 rebuilds each view on the new design system. Its legacy stylesheet is
 * imported only here, so it loads with this lazily loaded chunk and never with the public site.
 */
import React, { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
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
import { APP_BASE } from '../lib/routes'

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
    <>
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
                <Route path="health" element={<SystemHealthView />} />
                <Route path="*" element={<Navigate to={APP_BASE} replace />} />
              </Routes>
            </PageTransition>
          </main>
        </div>
      </div>
      <CommandPalette />
    </>
  )
}

export default Console
