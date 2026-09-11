import { NavLink } from 'react-router-dom'
import { NAV_ITEMS } from '../../navigation'
import { PaperModeBadge } from '../shared/PaperModeBadge'

export function Sidebar() {
  return (
    <aside className="hidden h-full w-56 flex-shrink-0 flex-col border-r border-border bg-surface/60 xl:flex">
      <div className="flex items-center gap-2 border-b border-border px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-md border border-accent-dim bg-accent/10" aria-hidden="true">
          <span className="font-mono text-sm font-bold text-accent">α</span>
        </div>
        <div className="leading-tight">
          <p className="text-sm font-bold tracking-[0.05em]">AEGIS ALPHA</p>
          <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-ink-muted">Intelligent Market Defense</p>
        </div>
      </div>

      <nav aria-label="Secondary navigation" className="flex-1 overflow-y-auto p-3 scrollbar-thin">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => (
            <li key={item.path}>
              <NavLink
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-md px-3 py-2.5 font-mono text-[11px] font-bold tracking-[0.04em] transition-colors ${
                    isActive
                      ? 'bg-accent/10 text-accent'
                      : 'text-ink-dim hover:bg-surface-3 hover:text-ink'
                  }`
                }
              >
                <item.icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="border-t border-border px-4 py-4">
        <PaperModeBadge />
        <p className="mt-3 font-mono text-[9px] leading-relaxed text-ink-muted">
          PAPER EXECUTION ONLY. No live trading is enabled by any agent.
        </p>
      </div>
    </aside>
  )
}