import { Link } from 'react-router-dom'
import { ArrowRight, ShieldCheck } from 'lucide-react'
import { Card } from '../shared/Card'
import { AegisMascot } from '../mascot/AegisMascot'
import type { AegisMascotState } from '../mascot/AegisMascot'
import { useGuardianData } from '../../hooks/useAppData'
import { StatusPill } from '../shared/StatusPill'
import type { ComponentHealth } from '../../types'

const MASCOT_BY_GUARDIAN: Record<string, AegisMascotState> = {
  ARMED: 'CONFIDENT',
  CAUTION: 'CAUTION',
  TRIPPED: 'DANGER',
  DATA_UNAVAILABLE: 'SCANNING',
}

export function BrainCard() {
  const guardian = useGuardianData()
  const guardianState = guardian.data?.state ?? 'DATA_UNAVAILABLE'
  const mascotState: AegisMascotState = MASCOT_BY_GUARDIAN[guardianState] ?? 'OFFLINE'

  const guardianHealth: ComponentHealth =
    guardianState === 'DATA_UNAVAILABLE'
      ? 'DATA_UNAVAILABLE'
      : guardianState === 'ARMED'
        ? 'HEALTHY'
        : guardianState === 'CAUTION'
          ? 'DEGRADED'
          : 'OFFLINE'

  return (
    <Card className="bg-surface md:col-span-2 xl:col-span-3">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md border border-accent-dim bg-accent/10">
            <span className="font-mono text-base font-bold text-accent">α</span>
          </div>
          <div>
            <p className="flex items-center gap-2 text-sm font-bold tracking-[0.02em]">
              AEGIS ALPHA ENGINE
              <span className="hidden rounded border border-border-subtle bg-surface-3 px-1.5 py-0.5 font-mono text-[9px] tracking-[0.08em] text-accent md:inline">
                INTELLIGENT MARKET DEFENSE
              </span>
            </p>
            <p className="font-mono text-[10px] text-ink-dim">PAPER-ONLY INTELLIGENCE &amp; SCANNING</p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <Link
            to="/guardian"
            className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold tracking-wide text-ink-dim transition-colors hover:text-ink"
          >
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            GUARDIAN
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </Link>
          <AegisMascot state={mascotState} />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 border-t border-border-subtle pt-4 md:grid-cols-4">
        <div>
          <span className="stat-label">Guardian</span>
          <div className="mt-1">
            <StatusPill status={guardianHealth} />
          </div>
        </div>
        <div>
          <span className="stat-label">Execution Mode</span>
          <p className="stat-value">PAPER ONLY</p>
        </div>
        <div>
          <span className="stat-label">Live Trading</span>
          <p className="stat-value text-loss">DISABLED</p>
        </div>
        <div>
          <span className="stat-label">Market Defense</span>
          <p className="stat-value text-accent">ACTIVE</p>
        </div>
      </div>
    </Card>
  )
}