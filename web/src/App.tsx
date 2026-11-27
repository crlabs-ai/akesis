import React, { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { ToastProvider } from './design'
import { APP_BASE, LEGACY_CONSOLE_PATHS } from './lib/routes'

// Each surface is its own chunk: visitors to the public site never download the console.
const LandingPage = lazy(() => import('./site/LandingPage'))
const Console = lazy(() => import('./console/Console'))
const LoginPage = lazy(() => import('./auth/LoginPage'))
const AuthCallback = lazy(() => import('./auth/AuthCallback'))

/** Old console URLs (/incidents/…, /health) keep working: same path, now under /app. */
function LegacyConsoleRedirect() {
  const { pathname, search, hash } = useLocation()
  return <Navigate to={`${APP_BASE}${pathname}${search}${hash}`} replace />
}

export function AppRoutes() {
  return (
    <Suspense fallback={<div className="ak" aria-busy="true" />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path={`${APP_BASE}/*`} element={<Console />} />
        {LEGACY_CONSOLE_PATHS.map((path) => (
          <Route key={path} path={`${path}/*`} element={<LegacyConsoleRedirect />} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}

export const App: React.FC = () => (
  <BrowserRouter>
    <ToastProvider>
      <AppRoutes />
    </ToastProvider>
  </BrowserRouter>
)

export default App
