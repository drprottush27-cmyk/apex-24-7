import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { Scanner } from './pages/Scanner'
import { Positions } from './pages/Positions'
import { Guardian } from './pages/Guardian'
import { Journal } from './pages/Journal'
import { Intelligence } from './pages/Intelligence'
import { Health } from './pages/Health'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="scanner" element={<Scanner />} />
        <Route path="positions" element={<Positions />} />
        <Route path="guardian" element={<Guardian />} />
        <Route path="journal" element={<Journal />} />
        <Route path="intelligence" element={<Intelligence />} />
        <Route path="health" element={<Health />} />
        <Route path="*" element={<Dashboard />} />
      </Route>
    </Routes>
  )
}