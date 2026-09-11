import { Pill } from './Pill'
import type { ComponentHealth } from '../../types'

const HEALTH_META: Record<ComponentHealth, { tone: 'green' | 'red' | 'amber' | 'blue'; label: string; dot: string }> = {
  HEALTHY: { tone: 'green', label: 'HEALTHY', dot: 'status-dot-green' },
  DEGRADED: { tone: 'amber', label: 'DEGRADED', dot: 'status-dot-amber' },
  OFFLINE: { tone: 'red', label: 'OFFLINE', dot: 'status-dot-red' },
  DATA_UNAVAILABLE: { tone: 'blue', label: 'DATA UNAVAILABLE', dot: 'status-dot-blue' },
}

interface StatusPillProps {
  status: ComponentHealth
}

export function StatusPill({ status }: StatusPillProps) {
  const meta = HEALTH_META[status]
  return (
    <Pill tone={meta.tone}>
      <span className={meta.dot} aria-hidden="true" />
      {meta.label}
    </Pill>
  )
}