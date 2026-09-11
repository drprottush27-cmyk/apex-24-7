import { TrendingUp } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { StatBlock } from '../shared/StatBlock'
import { usePerformanceData } from '../../hooks/useAppData'
import { fmtPct, fmtSignedUsd } from '../../utils/format'

export function PerformanceCard() {
  const data = usePerformanceData()
  const p = data.data

  const pnlTone = p && p.netPnl !== null ? (p.netPnl >= 0 ? 'green' : 'red') : 'default'

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
          <span className="section-label">Performance Summary</span>
        </div>
        <span className="font-mono text-[9px] text-ink-muted">PAPER ONLY</span>
      </div>
      <DataGate status={data.status} source={data.source}>
        <div className="mb-3 flex items-baseline justify-between gap-2 border-b border-border-subtle pb-3">
          <div>
            <span className="stat-label">Period</span>
            <p className="font-mono text-[11px] text-ink-dim">{p?.period ?? 'DATA UNAVAILABLE'}</p>
          </div>
          <div className="text-right">
            <span className="stat-label">Net PnL</span>
            <p className={`font-mono text-xl font-bold ${pnlTone === 'green' ? 'text-profit' : pnlTone === 'red' ? 'text-loss' : 'text-ink'}`}>
              {fmtSignedUsd(p?.netPnl)}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatBlock label="Net PnL %" value={fmtPct(p?.netPnlPct)} tone={pnlTone} />
          <StatBlock label="Win Rate" value={p?.winRate !== null && p?.winRate !== undefined ? `${p.winRate.toFixed(1)}%` : 'DATA UNAVAILABLE'} />
          <StatBlock label="Realized" value={fmtSignedUsd(p?.realizedPnl)} />
          <StatBlock label="Day Drawdown" value={fmtPct(p?.dayDrawdownPct)} tone={p && p.dayDrawdownPct !== null && p.dayDrawdownPct !== undefined && p.dayDrawdownPct < 0 ? 'red' : 'default'} />
        </div>
      </DataGate>
    </Card>
  )
}