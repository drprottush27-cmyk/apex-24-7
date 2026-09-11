import { useState } from 'react'
import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { Pill } from '../components/shared/Pill'
import { useScannerData } from '../hooks/useAppData'
import { fmtPrice } from '../utils/format'
import type { MarketRegimeLabel, RiskStatusLabel, TrendLabel } from '../types'

/* -------------------------------------------------------------------------- */
/* Column filter pills (UI only — no backend facet state yet)                 */
/* -------------------------------------------------------------------------- */

type VenueFilter = 'ALL' | 'BINANCE' | 'OKX' | 'SOLANA DEX' | 'BASE'

const VENUES: VenueFilter[] = ['ALL', 'BINANCE', 'OKX', 'SOLANA DEX', 'BASE']

function regimeTone(r: MarketRegimeLabel): 'green' | 'red' | 'amber' | 'blue' {
  switch (r) {
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

function trendTone(t: TrendLabel): 'green' | 'red' | 'blue' {
  switch (t) {
    case 'UP':
      return 'green'
    case 'DOWN':
      return 'red'
    default:
      return 'blue'
  }
}

function riskTone(r: RiskStatusLabel): 'green' | 'amber' | 'red' | 'blue' {
  switch (r) {
    case 'CLEAR':
      return 'green'
    case 'WATCH':
      return 'amber'
    case 'LIMITED':
    case 'BLOCKED':
      return 'red'
    default:
      return 'blue'
  }
}

export function Scanner() {
  const [query, setQuery] = useState('')
  const [venue, setVenue] = useState<VenueFilter>('ALL')
  const data = useScannerData()
  const rows = data.data ?? []

  const filtered = rows.filter((row) => {
    const matchQuery = !query || row.symbol.toUpperCase().includes(query.toUpperCase())
    const matchVenue = venue === 'ALL' || row.venue.toUpperCase().includes(venue.toUpperCase())
    return matchQuery && matchVenue
  })

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="SCANNER"
        title="Market Scanner"
        description="Future Aegis scanning integration point. Prepare for algorithmic opportunity detection."
      />

      {/* Search + filter controls */}
      <Card>
        <div className="flex flex-col gap-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by symbol…"
            className="w-full rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[11px] text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            aria-label="Search scanner symbols"
          />
          <div className="flex flex-wrap gap-1.5">
            {VENUES.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setVenue(v)}
                className={`rounded-md border px-2.5 py-1 font-mono text-[10px] font-bold tracking-wide transition-colors ${
                  venue === v
                    ? 'border-accent-dim bg-accent/10 text-accent'
                    : 'border-border-subtle bg-surface-2 text-ink-dim hover:border-border-focus hover:text-ink'
                }`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* Data table (desktop) / card list (mobile) */}
      <DataGate status={data.status} source={data.source}>
        {filtered.length === 0 ? (
          <div className="rounded-md border border-border-subtle bg-surface-2 px-4 py-6 text-center">
            <p className="font-mono text-[11px] font-bold text-ink-dim">NO MATCHING PAIRS</p>
            <p className="mt-1 font-mono text-[10px] text-ink-muted">Adjust filters or switch to ALL.</p>
          </div>
        ) : (
          <>
            {/* Mobile list */}
            <ul className="space-y-3 md:hidden">
              {filtered.map((row) => (
                <li key={row.symbol} className="card card-pad flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold">{row.symbol}</span>
                      <span className="font-mono text-[9px] text-ink-muted">{row.venue}</span>
                    </div>
                    <Pill tone={regimeTone(row.marketRegime)}>{row.marketRegime}</Pill>
                  </div>
                  <div className="grid grid-cols-3 gap-2 font-mono text-[10px]">
                    <div>
                      <span className="stat-label">Price</span>
                      <p className="text-ink-dim">{fmtPrice(row.price)}</p>
                    </div>
                    <div>
                      <span className="stat-label">RVOL</span>
                      <p className="text-ink-dim">{row.rvol !== null ? row.rvol.toFixed(2) : '—'}</p>
                    </div>
                    <div>
                      <span className="stat-label">Grade</span>
                      <p className="text-ink-dim">{row.setupGrade !== null ? row.setupGrade.toFixed(1) : '—'}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Pill tone={trendTone(row.trend)}>{row.trend}</Pill>
                    <Pill tone={riskTone(row.riskStatus)}>{row.riskStatus}</Pill>
                  </div>
                </li>
              ))}
            </ul>

            {/* Desktop table */}
            <div className="hidden md:block">
              <Card padded={false}>
                <div className="overflow-x-auto scrollbar-thin">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th className="pl-4">Symbol</th>
                        <th>Price</th>
                        <th>Regime</th>
                        <th>Trend</th>
                        <th>ADX</th>
                        <th>RSI</th>
                        <th>RVOL</th>
                        <th>Liquidity</th>
                        <th>Grade</th>
                        <th className="pr-4">Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((row) => (
                        <tr key={row.symbol}>
                          <td className="pl-4 text-ink">
                            {row.symbol} <span className="text-ink-muted">{row.venue}</span>
                          </td>
                          <td>{fmtPrice(row.price)}</td>
                          <td><Pill tone={regimeTone(row.marketRegime)}>{row.marketRegime}</Pill></td>
                          <td><Pill tone={trendTone(row.trend)}>{row.trend}</Pill></td>
                          <td>{row.adx !== null ? row.adx : '—'}</td>
                          <td>{row.rsi !== null ? row.rsi : '—'}</td>
                          <td>{row.rvol !== null ? row.rvol.toFixed(2) : '—'}</td>
                          <td>{row.liquidity}</td>
                          <td>{row.setupGrade !== null ? row.setupGrade.toFixed(1) : '—'}</td>
                          <td className="pr-4"><Pill tone={riskTone(row.riskStatus)}>{row.riskStatus}</Pill></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </>
        )}
      </DataGate>
    </div>
  )
}