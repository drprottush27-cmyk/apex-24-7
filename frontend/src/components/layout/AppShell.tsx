import { Outlet } from 'react-router-dom'
import { useMockMode } from '../../state/mockMode'
import { BottomNav } from './BottomNav'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

export function AppShell() {
  const { mockEnabled } = useMockMode()

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-bg">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        {mockEnabled ? (
          <div
            role="status"
            className="flex items-center justify-center gap-2 border-b border-info/30 bg-info/10 px-3 py-1.5 text-center"
          >
            <span className="font-mono text-[10px] font-bold tracking-[0.08em] text-info">
              DEVELOPMENT MOCK — FIXTURES ENABLED. NOT PRODUCTION DATA.
            </span>
          </div>
        ) : null}
        <TopBar />
        <main className="scrollbar-thin flex-1 overflow-y-auto px-4 pb-28 pt-4 md:px-6 md:pb-24 xl:px-8 xl:pb-8">
          <Outlet />
        </main>
        <BottomNav />
      </div>
    </div>
  )
}