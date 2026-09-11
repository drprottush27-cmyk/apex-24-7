import { Info } from 'lucide-react'
import { PageHeader } from '../components/shared/PageHeader'
import { Card } from '../components/shared/Card'
import { DataGate } from '../components/shared/DataGate'
import { Pill } from '../components/shared/Pill'
import { AegisMascot } from '../components/mascot/AegisMascot'
import type { AegisMascotState } from '../components/mascot/AegisMascot'
import { useIntelligenceData } from '../hooks/useAppData'

function mascotForDecision(decision: string): AegisMascotState {
  switch (decision) {
    case 'APPROVED FOR PAPER':
      return 'CONFIDENT'
    case 'REJECTED':
      return 'DANGER'
    case 'PENDING':
      return 'SCANNING'
    default:
      return 'SCANNING'
  }
}

export function Intelligence() {
  const data = useIntelligenceData()
  const report = data.data
  const mascotState = mascotForDecision(report?.guardianDecision ?? 'DATA_UNAVAILABLE')

  const decisionTone =
    report?.guardianDecision === 'APPROVED FOR PAPER'
      ? 'green'
      : report?.guardianDecision === 'REJECTED'
        ? 'red'
        : 'amber'

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        eyebrow="AEGIS INTELLIGENCE"
        title="Market Intelligence"
        description="Compact advisory panel — not a chat interface. No execution authority is implied."
        right={<AegisMascot state={mascotState} />}
      />

      <DataGate status={data.status} source={data.source}>
        <Card>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="stat-label">Market Regime</span>
              <p className="mt-0.5 text-lg font-bold">{report?.regime ?? 'DATA UNAVAILABLE'}</p>
            </div>
            <div className="rounded-md border border-border-subtle bg-surface-2 px-3 py-2">
              <span className="stat-label">Market Summary</span>
              <p className="mt-0.5 font-mono text-[11px] text-ink-dim">
                {report?.marketSummary ?? 'Summary pending backend integration.'}
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <span className="stat-label mb-2 block">Setup Explanation</span>
              <p className="rounded-md border border-border-subtle bg-surface-2 px-3 py-3 font-mono text-[11px] leading-relaxed text-ink-dim">
                {report?.setupExplanation ?? 'Explanation pending backend integration.'}
              </p>
            </div>
            <div className="grid gap-4">
              <div>
                <span className="stat-label mb-2 block">Supporting Factors</span>
                <ul className="space-y-1.5">
                  {(report?.supportingFactors ?? []).map((factor) => (
                    <li key={factor} className="flex items-center gap-2 font-mono text-[11px] text-profit">
                      <span aria-hidden="true">✓</span> {factor}
                    </li>
                  ))}
                  {report && report.supportingFactors.length === 0 ? (
                    <li className="font-mono text-[11px] text-ink-muted">No factors recorded.</li>
                  ) : null}
                </ul>
              </div>
              <div>
                <span className="stat-label mb-2 block">Risk Factors</span>
                <ul className="space-y-1.5">
                  {(report?.riskFactors ?? []).map((factor) => (
                    <li key={factor} className="flex items-center gap-2 font-mono text-[11px] text-warning">
                      <span aria-hidden="true">⚠</span> {factor}
                    </li>
                  ))}
                  {report && report.riskFactors.length === 0 ? (
                    <li className="font-mono text-[11px] text-ink-muted">No risk factors recorded.</li>
                  ) : null}
                </ul>
              </div>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="section-label">Guardian Decision</span>
              <Pill tone={decisionTone === 'amber' ? 'amber' : decisionTone === 'green' ? 'green' : 'red'}>
                {report?.guardianDecision ?? 'DATA UNAVAILABLE'}
              </Pill>
            </div>
          </div>
          <p className="mt-3 flex items-start gap-2 font-mono text-[10px] leading-relaxed text-ink-muted">
            <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
            Advisory content is produced for review purposes. Approval to act in paper rests exclusively with the
            deterministic Risk Guardian, never with the assistant.
          </p>
        </Card>
      </DataGate>
    </div>
  )
}