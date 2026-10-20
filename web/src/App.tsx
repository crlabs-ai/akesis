import React, { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { NavRail } from './components/Layout/NavRail'
import { Header } from './components/Layout/Header'
import { OverviewView } from './components/Overview/OverviewView'
import { IncidentsList } from './components/Incidents/IncidentsList'
import { IncidentDetailWorkspace } from './components/IncidentDetail/IncidentDetailWorkspace'
import { PipelineExplorer } from './components/Pipelines/PipelineExplorer'
import { SystemHealthView } from './components/Health/SystemHealthView'
import { PageTransition } from './components/Layout/PageTransition'

export const App: React.FC = () => {
  const [refreshKey, setRefreshKey] = useState<number>(0)
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false)

  const handleManualRefresh = () => {
    setIsRefreshing(true)
    setRefreshKey((prev) => prev + 1)
    setTimeout(() => setIsRefreshing(false), 500)
  }

  return (
    <BrowserRouter>
      <div className="app-shell">
        <NavRail />
        <div className="main-wrapper">
          <Header onRefresh={handleManualRefresh} isRefreshing={isRefreshing} />
          <main className="content-area" key={refreshKey}>
            <PageTransition>
              <Routes>
                <Route path="/" element={<OverviewView />} />
                <Route path="/incidents" element={<IncidentsList />} />
                <Route path="/incidents/:incidentId" element={<IncidentDetailWorkspace />} />
                <Route path="/pipelines" element={<PipelineExplorer />} />
                <Route path="/health" element={<SystemHealthView />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </PageTransition>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}

export default App
