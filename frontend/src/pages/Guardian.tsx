import { AlertTriangle, Shield } from 'lucide-react'
import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { StatBlock } from '../components/shared/StatBlock'
import { StatusPill } from '../components/shared/StatusPill'
import { useGuardianData } from '../hooks/useAppData'
import { fmtPct } from '../utils/format'
import type { ComponentHealth, GuardianStatus } from '../types'

function guardianHealth(state: GuardianStatus['state']): ComponentHealth {
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

export function Guardian() {
  const data = useGuardianData()
  const g = data.data

  const healthStatus: ComponentHealth = guardianHealth(g?.state ?? 'DATA_UNAVAILABLE')

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="RISK GUARDIAN"
        title="Deterministic Risk Control"
        description="Risk Guardian is deterministic. Proposal authority is advisory; the final veto is never overridable."
      />

      <Card>
        <div className="mb-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-accent" aria-hidden="true" />
            <span className="section-label">Guardian Status</span>
          </div>
          <StatusPill status={healthStatus} />
        </div>

        <DataGate status={data.status} source={data.source}>
          <div className="grid grid-cols-2 gap-x-4 gap-y-5 md:grid-cols-4">
            <StatBlock label="Circuit Breaker" value={<span className={g?.circuitBreaker === 'ENGAGED' ? 'text-loss' : 'text-profit'}>{g?.circuitBreaker ?? 'DATA UNAVAILABLE'}</span>} />
            <StatBlock
              label="Daily Drawdown"
              value={fmtPct(g?.dailyDrawdownPct)}
              tone={g && g.dailyDrawdownPct !== null && g.dailyDrawdownPct < 0 ? 'red' : 'default'}
            />
            <StatBlock label="Max Daily Drawdown" value={fmtPct(g?.maxDailyDrawdownPct)} />
            <StatBlock
              label="Active Risk"
              value={fmtPct(g?.activeRiskPct)}
              tone={g && g.activeRiskPct !== null && g.activeRiskPct > 0 ? 'amber' : 'default'}
            />
            <StatBlock
              label="Open Position Count"
              value={g?.openPositionCount !== null && g?.openPositionCount !== undefined ? String(g.openPositionCount) : 'DATA UNAVAILABLE'}
            />
            <StatBlock
              label="Max Position Count"
              value={g?.maxPositionCount !== null && g?.maxPositionCount !== undefined ? String(g.maxPositionCount) : 'DATA UNAVAILABLE'}
            />
            <StatBlock
              label="Portfolio Exposure"
              value={fmtPct(g?.portfolioExposurePct)}
              tone={g && g.portfolioExposurePct !== null && g.portfolioExposurePct > 0 ? 'amber' : 'default'}
            />
          </div>
        </DataGate>
      </Card>

      {/* Emergency controls — visually distinct, disabled, backend-controlled */}
      <Card className="border-loss/40">
        <div className="mb-3 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-loss" aria-hidden="true" />
          <span className="section-label text-loss">Emergency Controls</span>
        </div>
        <p className="mb-3 font-mono text-[10px] leading-relaxed text-ink-muted">
          These controls are displayed for audit transparency. Activation requires the deterministic backend
          Risk Guardian and execution safety layer — this interface has no authority to act.
        </p>
        <div className="flex flex-col gap-3">
          <span className="emergency-control" aria-disabled="true">
            <Shield className="h-3.5 w-3.5" aria-hidden="true" />
            TRIP CIRCUIT BREAKER — BACKEND CONTROL REQUIRED
          </span>
          <span className="emergency-control" aria-disabled="true">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            EMERGENCY FLAT ALL — BACKEND CONTROL REQUIRED
          </span>
        </div>
      </Card>
    </div>
  )
}