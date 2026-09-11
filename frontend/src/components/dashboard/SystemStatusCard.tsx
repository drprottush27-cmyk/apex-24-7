import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { Card } from '../shared/Card'
import { StatusPill } from '../shared/StatusPill'
import { DataGate } from '../shared/DataGate'
import { useSystemHealthData } from '../../hooks/useAppData'

const COMPONENTS = [
  { key: 'scanner', label: 'Scanner' },
  { key: 'marketData', label: 'Market Data' },
  { key: 'api', label: 'API' },
  { key: 'riskGuardian', label: 'Risk Guardian' },
  { key: 'paperEngine', label: 'Paper Engine' },
] as const

export function SystemStatusCard() {
  const data = useSystemHealthData()

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <span className="section-label">System Status</span>
        <Link to="/health" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          HEALTH <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        <ul className="space-y-2">
          {COMPONENTS.map((c) => (
            <li key={c.key} className="flex items-center justify-between gap-3 border-b border-border-subtle pb-2 last:border-none last:pb-0">
              <span className="font-mono text-[11px] text-ink-dim">{c.label}</span>
              <StatusPill status={data.data?.components[c.key] ?? 'DATA_UNAVAILABLE'} />
            </li>
          ))}
        </ul>
        <div className="mt-3 grid grid-cols-2 gap-2 border-t border-border-subtle pt-3">
          <div>
            <span className="stat-label">Last Scan</span>
            <p className="stat-value text-xs text-ink-dim">{data.data?.lastScan ?? 'DATA UNAVAILABLE'}</p>
          </div>
          <div>
            <span className="stat-label">Last Update</span>
            <p className="stat-value text-xs text-ink-dim">{data.data?.lastUpdate ?? 'DATA UNAVAILABLE'}</p>
          </div>
        </div>
      </DataGate>
    </Card>
  )
}