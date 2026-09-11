import { Link } from 'react-router-dom'
import { ArrowRight, Radar } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { Pill } from '../shared/Pill'
import { useScannerData } from '../../hooks/useAppData'
import { fmtPrice } from '../../utils/format'
import type { RiskStatusLabel } from '../../types'

function riskTone(risk: RiskStatusLabel): 'green' | 'red' | 'amber' | 'blue' | 'neutral' {
  switch (risk) {
    case 'CLEAR':
      return 'green'
    case 'WATCH':
      return 'amber'
    case 'LIMITED':
      return 'red'
    case 'BLOCKED':
      return 'red'
    default:
      return 'blue'
  }
}

export function ScannerHighlightsCard() {
  const data = useScannerData()
  const rows = data.data?.slice(0, 4) ?? []

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <span className="section-label">Scanner Highlights</span>
        <Link to="/scanner" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          <Radar className="h-3.5 w-3.5" aria-hidden="true" />
          FULL SCANNER <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.symbol} className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-bold">{row.symbol}</p>
                <p className="font-mono text-[10px] text-ink-muted">
                  RVOL {row.rvol ? row.rvol.toFixed(2) : '—'} · ADX {row.adx ?? '—'} · RSI {row.rsi ?? '—'}
                </p>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="font-mono text-[11px] text-ink-dim">{fmtPrice(row.price)}</span>
                <Pill tone={riskTone(row.riskStatus)}>{row.riskStatus}</Pill>
              </div>
            </li>
          ))}
        </ul>
      </DataGate>
    </Card>
  )
}