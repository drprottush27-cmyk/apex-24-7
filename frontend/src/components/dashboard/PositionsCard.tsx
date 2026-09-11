import { Link } from 'react-router-dom'
import { ArrowRight, LayoutList } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { DirBadge } from '../shared/DirBadge'
import { Pill } from '../shared/Pill'
import { usePositionsData } from '../../hooks/useAppData'
import { fmtPrice, fmtSignedUsd } from '../../utils/format'

export function PositionsCard() {
  const data = usePositionsData()
  const rows = data.data ?? []
  const openCount = rows.filter((p) => p.status === 'OPEN').length

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="section-label">Active Paper Positions</span>
          <Pill tone="amber">PAPER</Pill>
        </div>
        <Link to="/positions" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          <LayoutList className="h-3.5 w-3.5" aria-hidden="true" /> POSITIONS <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        {rows.length === 0 ? (
          <p className="font-mono text-[11px] text-ink-muted">No paper positions recorded.</p>
        ) : (
          <ul className="space-y-2">
            {rows.slice(0, 3).map((pos) => (
              <li key={pos.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border-subtle bg-surface-2 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <DirBadge side={pos.side} />
                  <span className="text-sm font-bold">{pos.symbol}</span>
                </div>
                <div className="flex items-center gap-3 font-mono text-[10px]">
                  <span className="text-ink-dim">
                    {fmtPrice(pos.entry)} → {fmtPrice(pos.currentPrice)}
                  </span>
                  <span className={pos.unrealizedPnl !== null && pos.unrealizedPnl >= 0 ? 'text-profit' : 'text-loss'}>
                    {fmtSignedUsd(pos.unrealizedPnl)}
                  </span>
                </div>
              </li>
            ))}
            <p className="font-mono text-[10px] text-ink-muted">{openCount} open paper position(s)</p>
          </ul>
        )}
      </DataGate>
    </Card>
  )
}