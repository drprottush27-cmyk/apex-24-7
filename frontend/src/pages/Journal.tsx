import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { Pill } from '../components/shared/Pill'
import { DirBadge } from '../components/shared/DirBadge'
import { EmptyState } from '../components/shared/EmptyState'
import { useJournalData } from '../hooks/useAppData'
import { fmtDate, fmtPrice, fmtRatio, fmtSignedUsd } from '../utils/format'

export function Journal() {
  const data = useJournalData()
  const rows = data.data ?? []

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="JOURNAL"
        title="Trade Journal"
        description="Audit trail of paper trades. Empty states are shown honestly until the paper engine records trades."
      />

      <DataGate status={data.status} source={data.source}>
        {rows.length === 0 ? (
          <EmptyState
            title="No trades recorded"
            description="The journal is populated by the paper engine after backend integration. Nothing is invented here."
          />
        ) : (
          <Card padded={false}>
            <div className="overflow-x-auto scrollbar-thin">
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="pl-4">Trade ID</th>
                    <th>Symbol</th>
                    <th>Direction</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>PnL</th>
                    <th>R:R</th>
                    <th>Status</th>
                    <th>Open Time</th>
                    <th className="pr-4">Close Time</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.tradeId}>
                      <td className="pl-4 text-ink-muted">{row.tradeId}</td>
                      <td className="text-ink">{row.symbol}</td>
                      <td><DirBadge side={row.direction} /></td>
                      <td>{fmtPrice(row.entry)}</td>
                      <td>{fmtPrice(row.exit)}</td>
                      <td className={row.pnl !== null && row.pnl >= 0 ? 'text-profit' : 'text-loss'}>
                        {fmtSignedUsd(row.pnl)}
                      </td>
                      <td>{fmtRatio(row.riskReward)}</td>
                      <td>
                        <Pill tone={row.status === 'OPEN' ? 'green' : row.status === 'CLOSED' ? 'blue' : 'red'}>
                          {row.status}
                        </Pill>
                      </td>
                      <td>{fmtDate(row.openTime)}</td>
                      <td className="pr-4">{fmtDate(row.closeTime)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </DataGate>
    </div>
  )
}