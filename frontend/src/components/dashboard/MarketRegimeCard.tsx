import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { Pill } from '../shared/Pill'
import { useScannerData } from '../../hooks/useAppData'
import { fmtPrice } from '../../utils/format'
import type { MarketRegimeLabel } from '../../types'

function regimeTone(regime: MarketRegimeLabel): 'green' | 'red' | 'amber' | 'blue' {
  switch (regime) {
    case 'BULLISH TREND':
    case 'BREAKOUT':
      return 'green'
    case 'BEARISH TREND':
    case 'BREAKDOWN':
      return 'red'
    case 'RANGING':
      return 'amber'
    default:
      return 'blue'
  }
}

export function MarketRegimeCard() {
  const data = useScannerData()
  const rows = data.data?.slice(0, 3) ?? []

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <span className="section-label">Market Regime</span>
        <Link to="/scanner" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          SCANNER <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.symbol} className="flex items-center justify-between gap-3 rounded-md border border-border-subtle bg-surface-2 px-3 py-2.5">
              <div>
                <p className="flex items-center gap-2 text-sm font-bold">
                  {row.symbol}
                  <span className="font-mono text-[9px] text-ink-muted">{row.venue}</span>
                </p>
                <p className="font-mono text-[10px] text-ink-dim">{fmtPrice(row.price)}</p>
              </div>
              <Pill tone={regimeTone(row.marketRegime)}>{row.marketRegime}</Pill>
            </li>
          ))}
        </ul>
      </DataGate>
    </Card>
  )
}