import { Link } from 'react-router-dom'
import { ArrowRight, Shield } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { StatusPill } from '../shared/StatusPill'
import { useGuardianData } from '../../hooks/useAppData'
import { fmtPct } from '../../utils/format'
import type { ComponentHealth } from '../../types'

function toComponentHealth(state: string): ComponentHealth {
  switch (state) {
    case 'ARMED':
      return 'HEALTHY'
    case 'CAUTION':
      return 'DEGRADED'
    case 'TRIPPED':
      return 'OFFLINE'
    default:
      return 'DATA_UNAVAILABLE'
  }
}

export function RiskGuardianCard() {
  const data = useGuardianData()
  const g = data.data
  const guardianHealth = toComponentHealth(g?.state ?? 'DATA_UNAVAILABLE')

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
          <span className="section-label">Risk Guardian</span>
        </div>
        <Link to="/guardian" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          GUARDIAN <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        <div className="mb-2">
          <div className="flex items-center justify-between">
            <span className="stat-label">Guardian Status</span>
            <StatusPill status={guardianHealth} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <span className="stat-label">Active Risk</span>
              <p className="stat-value">{fmtPct(g?.activeRiskPct)}</p>
            </div>
            <div>
              <span className="stat-label">Daily Drawdown</span>
              <p className={`stat-value ${g && g.dailyDrawdownPct !== null && g.dailyDrawdownPct < 0 ? 'text-loss' : ''}`}>
                {fmtPct(g?.dailyDrawdownPct)}
              </p>
            </div>
          </div>
        </div>
      </DataGate>
    </Card>
  )
}