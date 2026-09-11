import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { FlaskConical, LayoutGrid } from 'lucide-react'
import { NAV_ITEMS } from '../../navigation'
import { useMockMode } from '../../state/mockMode'
import { PaperModeBadge } from '../shared/PaperModeBadge'

function BrandMark() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface-2" aria-hidden="true">
        <svg fill="none" stroke="currentColor" strokeWidth="2.2" viewBox="0 0 24 24" className="h-4 w-4 text-accent">
          <path d="m3 16 7-10 4 6 7-8" />
          <path d="M17 4h4v4" />
        </svg>
      </div>
      <div className="leading-tight">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-[0.05em]">AEGIS ALPHA</span>
          <span className="rounded border border-border-subtle bg-surface-3 px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.08em] text-accent">
            INTELLIGENT MARKET DEFENSE
          </span>
        </div>
        <p className="font-mono text-[9px] uppercase tracking-[0.15em] text-ink-muted">Institutional Terminal</p>
      </div>
    </div>
  )
}

function QuickSwitcher() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Open all screens"
        className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-ink-dim transition-colors hover:border-border-focus hover:text-ink"
      >
        <LayoutGrid className="h-4 w-4" aria-hidden="true" />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-10 z-40 w-56 rounded-md border border-border bg-surface-2 p-1 shadow-subtle"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              role="menuitem"
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded px-2.5 py-2 font-mono text-[11px] font-semibold ${
                  isActive ? 'bg-accent/10 text-accent' : 'text-ink-dim hover:bg-surface-3 hover:text-ink'
                }`
              }
            >
              <item.icon className="h-3.5 w-3.5" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function MockToggle() {
  const { mockEnabled, setMockEnabled } = useMockMode()
  return (
    <button
      type="button"
      onClick={() => setMockEnabled(!mockEnabled)}
      aria-pressed={mockEnabled}
      title="Toggle clearly-labelled development fixtures"
      className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[9px] font-bold tracking-[0.05em] transition-colors ${
        mockEnabled
          ? 'border-info/40 bg-info/10 text-info'
          : 'border-border bg-surface-2 text-ink-dim hover:border-border-focus hover:text-ink'
      }`}
    >
      <FlaskConical className="h-3 w-3" aria-hidden="true" />
      DEV MOCK
    </button>
  )
}

export function TopBar() {
  return (
    <header className="z-30 flex h-14 flex-shrink-0 items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-6">
      <BrandMark />
      <div className="flex items-center gap-2 md:gap-3">
        <MockToggle />
        <PaperModeBadge />
        <QuickSwitcher />
      </div>
    </header>
  )
}