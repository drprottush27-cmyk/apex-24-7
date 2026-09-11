import { NavLink } from 'react-router-dom'
import { PRIMARY_NAV } from '../../navigation'

export function BottomNav() {
  return (
    <nav
      aria-label="Primary navigation"
      className="safe-bottom fixed inset-x-0 bottom-0 z-30 flex h-16 items-stretch justify-between border-t border-border bg-surface/95 px-1 backdrop-blur xl:hidden"
    >
      {PRIMARY_NAV.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.path === '/'}
          className={({ isActive }) =>
            `flex flex-1 flex-col items-center justify-center gap-1 rounded-md py-1.5 transition-colors ${
              isActive ? 'text-accent' : 'text-ink-muted hover:text-ink'
            }`
          }
        >
          {({ isActive }) => (
            <>
              <item.icon className="h-5 w-5" strokeWidth={isActive ? 2.4 : 2} aria-hidden="true" />
              <span
                className={`font-mono text-[9px] font-bold tracking-[0.06em] ${
                  isActive ? 'text-accent' : ''
                }`}
              >
                {item.label}
              </span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}