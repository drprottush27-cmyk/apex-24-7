import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { StatusPill } from '../components/shared/StatusPill'
import { useSystemHealthData } from '../hooks/useAppData'

const COMPONENTS = [
  { key: 'scanner', label: 'Scanner' },
  { key: 'marketData', label: 'Market Data' },
  { key: 'api', label: 'API' },
  { key: 'riskGuardian', label: 'Risk Guardian' },
  { key: 'paperEngine', label: 'Paper Engine' },
] as const

const PLANNED_ENDPOINTS = [
  { method: 'GET', endpoint: '/api/v1/health', note: 'exists in backend (webhook_server.py)' },
  { method: 'GET', endpoint: '/api/v1/system/status', note: 'planned' },
  { method: 'GET', endpoint: '/api/v1/scanner', note: 'planned' },
  { method: 'GET', endpoint: '/api/v1/positions', note: 'planned' },
  { method: 'GET', endpoint: '/api/v1/guardian', note: 'planned' },
  { method: 'GET', endpoint: '/api/v1/performance', note: 'planned' },
]

export function Health() {
  const data = useSystemHealthData()

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="SYSTEM HEALTH"
        title="Backend Readiness"
        description="Readiness surfaces for the Aegis backend. States are honest: HEALTHY only when the wire protocol reports it."
      />

      <Card>
        <div className="mb-3">
          <span className="section-label">Core Components</span>
        </div>
        <DataGate status={data.status} source={data.source}>
          <ul className="space-y-2.5">
            {COMPONENTS.map((c) => (
              <li key={c.key} className="flex items-center justify-between gap-3 border-b border-border-subtle pb-2.5 last:border-none last:pb-0">
                <span className="font-mono text-[11px] text-ink-dim">{c.label}</span>
                <StatusPill status={data.data?.components[c.key] ?? 'DATA_UNAVAILABLE'} />
              </li>
            ))}
          </ul>
          <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border-subtle pt-4">
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

      <Card>
        <div className="mb-3">
          <span className="section-label">Prepared API Surface</span>
          <p className="mt-1 font-mono text-[10px] text-ink-muted">
            Typed client is defined in frontend/src/api/. No endpoint is called automatically in Phase UI-1.
          </p>
        </div>
        <ul className="space-y-1.5">
          {PLANNED_ENDPOINTS.map((ep) => (
            <li key={ep.endpoint} className="flex items-center gap-3 font-mono text-[11px]">
              <span className="w-9 rounded border border-border-subtle bg-surface-2 px-1.5 py-0.5 text-center text-[9px] font-bold text-accent">
                {ep.method}
              </span>
              <span className="text-ink-dim">{ep.endpoint}</span>
              <span className="ml-auto text-[9px] text-ink-muted">{ep.note}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}