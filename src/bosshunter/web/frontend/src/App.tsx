import { BrowserRouter, HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import DashboardPage from './pages/DashboardPage'
import ConfigPage from './pages/ConfigPage'
import JudgeDemoPage from './pages/JudgeDemoPage'

function JobsPage() {
  return <DashboardPage view="jobs" />
}

function MonitorPage() {
  return <DashboardPage view="monitor" />
}

export default function App() {
  const demoOnly = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env.VITE_DEMO_ONLY === '1'
  const Router = demoOnly ? HashRouter : BrowserRouter

  return (
    <Router>
      <Routes>
        <Route path="/demo" element={<JudgeDemoPage />} />
        {demoOnly ? (
          <Route path="*" element={<Navigate to="/demo" replace />} />
        ) : (
          <Route path="*" element={
            <div className="flex h-screen overflow-hidden bg-background text-foreground">
              <Sidebar />
              <div className="flex-1 flex flex-col overflow-hidden">
                <Header />
                <main className="flex-1 overflow-y-auto p-6">
                  <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/monitor" element={<MonitorPage />} />
              <Route path="/config" element={<ConfigPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </main>
              </div>
            </div>
          } />
        )}
      </Routes>
    </Router>
  )
}
