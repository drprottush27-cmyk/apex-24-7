import { BrainCard } from '../components/dashboard/BrainCard'
import { SystemStatusCard } from '../components/dashboard/SystemStatusCard'
import { MarketRegimeCard } from '../components/dashboard/MarketRegimeCard'
import { ScannerHighlightsCard } from '../components/dashboard/ScannerHighlightsCard'
import { PositionsCard } from '../components/dashboard/PositionsCard'
import { RiskGuardianCard } from '../components/dashboard/RiskGuardianCard'
import { RecentSignalsCard } from '../components/dashboard/RecentSignalsCard'
import { PerformanceCard } from '../components/dashboard/PerformanceCard'
import { IntelligenceSummaryCard } from '../components/dashboard/IntelligenceSummaryCard'

export function Dashboard() {
  return (
    <div className="flex flex-col gap-4">
      <BrainCard />
      <div className="grid gap-4 md:grid-cols-2">
        <SystemStatusCard />
        <MarketRegimeCard />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <PositionsCard />
        <RiskGuardianCard />
      </div>
      <ScannerHighlightsCard />
      <div className="grid gap-4 md:grid-cols-2">
        <PerformanceCard />
        <IntelligenceSummaryCard />
      </div>
      <RecentSignalsCard />
    </div>
  )
}