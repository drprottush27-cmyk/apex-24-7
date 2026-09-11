import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { Pill } from '../components/shared/Pill'
import { DirBadge } from '../components/shared/DirBadge'
import { EmptyState } from '../components/shared/EmptyState'
import { usePositionsData } from '../hooks/useAppData'
import { fmtPrice, fmtRatio, fmtSignedUsd } from '../utils/format'

function statusTone(status: 'OPEN' | 'CLOSED' | 'DATA_UNAVAILABLE'): 'green' | 'blue' | 'red' {
  switch (status) {
    case 'OPEN':
      return 'green'
    case 'CLOSED':
      return 'blue'
    default:
      return 'red'
  }
}

export function Positions() {
  const data = usePositionsData()
  const rows = data.data ?? []

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="POSITIONS"
        title="Paper Positions"
        description="Simulated paper positions only — nothing here touches an exchange account."
        right={<Pill tone="amber">PAPER POSITIONS</Pill>}
      />

      <DataGate status={data.status} source={data.source}>
        {rows.length === 0 ? (
          <EmptyState
            title="No paper positions"
            description="The paper engine records positions here once backend integration is enabled."
          />
        ) : (
          <>
            {/* Mobile list */}
            <ul className="space-y-3 md:hidden">
              {rows.map((pos) => (
                <li key={pos.id} className="card card-pad">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <DirBadge side={pos.side} />
                      <span className="text-sm font-bold">{pos.symbol}</span>
                    </div>
                    <Pill tone={statusTone(pos.status)}>{pos.status}</Pill>
                  </div>
                  <div className="grid grid-cols-3 gap-2 font-mono text-[10px]">
                    <div>
                      <span className="stat-label">Entry</span>
                      <p className="text-ink-dim">{fmtPrice(pos.entry)}</p>
                    </div>
                    <div>
                      <span className="stat-label">Current</span>
                      <p className="text-ink-dim">{fmtPrice(pos.currentPrice)}</p>
                    </div>
                    <div>
                      <span className="stat-label">R:R</span>
                      <p className="text-ink-dim">{fmtRatio(pos.riskReward)}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-2">
                    <span className="font-mono text-[10px] text-ink-muted">
                      SL {fmtPrice(pos.stopLoss)} · TP {fmtPrice(pos.takeProfit)}
                    </span>
                    <span className={`font-mono text-[11px] font-bold ${pos.unrealizedPnl !== null && pos.unrealizedPnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {fmtSignedUsd(pos.unrealizedPnl)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>

            {/* Desktop table */}
            <Card padded={false}>
              <div className="overflow-x-auto scrollbar-thin">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th className="pl-4">ID</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Entry</th>
                      <th>Current</th>
                      <th>Stop Loss</th>
                      <th>Take Profit</th>
                      <th>R:R</th>
                      <th>Unrealized PnL</th>
                      <th className="pr-4">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((pos) => (
                      <tr key={pos.id}>
                        <td className="pl-4 text-ink-muted">{pos.id}</td>
                        <td className="text-ink">{pos.symbol}</td>
                        <td><DirBadge side={pos.side} /></td>
                        <td>{fmtPrice(pos.entry)}</td>
                        <td>{fmtPrice(pos.currentPrice)}</td>
                        <td>{fmtPrice(pos.stopLoss)}</td>
                        <td>{fmtPrice(pos.takeProfit)}</td>
                        <td>{fmtRatio(pos.riskReward)}</td>
                        <td className={pos.unrealizedPnl !== null && pos.unrealizedPnl >= 0 ? 'text-profit' : 'text-loss'}>
                          {fmtSignedUsd(pos.unrealizedPnl)}
                        </td>
                        <td className="pr-4">
                          <Pill tone={statusTone(pos.status)}>{pos.status}</Pill>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </DataGate>

      <p className="font-mono text-[10px] leading-relaxed text-ink-muted">
        Position lifecycle, stop loss enforcement and risk limits are controlled by the hardened Risk Engine —
        never by the interface. UI controls for lifecycle actions remain disabled until backend wiring.
      </p>
    </div>
  )
}