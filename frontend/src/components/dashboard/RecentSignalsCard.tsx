import { Link } from 'react-router-dom'
import { ArrowRight, BellRing } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { EmptyState } from '../shared/EmptyState'

export function RecentSignalsCard() {
  // No signal fixture exists yet — signals will stream from the backend in a
  // future phase. Until then this is an honest empty state, never fabricated.
  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BellRing className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
          <span className="section-label">Recent Signals</span>
        </div>
        <span className="font-mono text-[9px] text-ink-muted">BACKEND PENDING</span>
      </div>
      <DataGate status="UNAVAILABLE" source="none">
        <EmptyState
          title="No signals recorded"
          description="Backend integration is pending. Signals will appear here once the wire protocol is enabled."
        />
      </DataGate>
      <div className="mt-3">
        <Link to="/positions" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          OPEN PAPER POSITIONS <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
    </Card>
  )
}