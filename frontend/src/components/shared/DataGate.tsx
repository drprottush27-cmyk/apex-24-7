import type { ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import type { AsyncStatus, DataSource } from '../../types'

interface DataGateProps {
  status: AsyncStatus
  source: DataSource
  children: ReactNode
  /** What to render while LOADING. */
  loading?: ReactNode
  /** What to render when UNAVAILABLE. Overrides default honest placeholder. */
  unavailable?: ReactNode
  /** Compact mode for tight terminal rows. */
  compact?: boolean
}

const DEFAULT_LOADING = (
  <div className="flex items-center gap-2 text-ink-muted">
    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
    <span className="font-mono text-[11px] tracking-wide">LOADING DATA</span>
  </div>
)

const DEFAULT_UNAVAILABLE = (
  <div className="flex items-center justify-between gap-2 rounded-md border border-border-subtle bg-surface-2 px-3 py-3">
    <div>
      <p className="font-mono text-[11px] font-bold tracking-[0.06em] text-ink-dim">DATA UNAVAILABLE</p>
      <p className="mt-0.5 font-mono text-[10px] text-ink-muted">Backend integration is pending.</p>
    </div>
    <span className="status-dot-dim" aria-hidden="true" />
  </div>
)

/**
 * Honest data gate. Renders `children` only when data is AVAILABLE. It never
 * fabricates content for missing data. When `source === 'devmock'` a persistent
 * DEVELOPMENT MOCK tag is attached so fixtures can never be mistaken for real
 * production data.
 */
export function DataGate({
  status,
  source,
  children,
  loading,
  unavailable,
  compact = false,
}: DataGateProps) {
  if (status === 'LOADING') {
    return <>{loading ?? DEFAULT_LOADING}</>
  }

  if (status === 'UNAVAILABLE' || source === 'none') {
    return <>{unavailable ?? DEFAULT_UNAVAILABLE}</>
  }

  const mockTag = source === 'devmock' ? (
    <span className="inline-flex items-center rounded border border-info/40 bg-info/10 px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-[0.05em] text-info">
      DEVELOPMENT MOCK
    </span>
  ) : null

  return (
    <div className={compact ? '' : ''}>
      {mockTag}
      <div className="mt-1">{children}</div>
    </div>
  )
}