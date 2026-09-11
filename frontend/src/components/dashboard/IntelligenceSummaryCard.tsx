import { Link } from 'react-router-dom'
import { ArrowRight, Brain } from 'lucide-react'
import { Card } from '../shared/Card'
import { DataGate } from '../shared/DataGate'
import { Pill } from '../shared/Pill'
import { useIntelligenceData } from '../../hooks/useAppData'

export function IntelligenceSummaryCard() {
  const data = useIntelligenceData()
  const report = data.data

  const guardianTag =
    report?.guardianDecision === 'APPROVED FOR PAPER'
      ? ({ tone: 'green' as const, label: 'APPROVED FOR PAPER' })
      : report?.guardianDecision === 'REJECTED'
        ? ({ tone: 'red' as const, label: 'REJECTED' })
        : ({ tone: 'amber' as const, label: report?.guardianDecision ?? 'DATA UNAVAILABLE' })

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
          <span className="section-label">Aegis Intelligence Summary</span>
        </div>
        <Link to="/intelligence" className="inline-flex items-center gap-1 font-mono text-[10px] font-semibold text-accent hover:underline">
          AEGIS <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      </div>
      <DataGate status={data.status} source={data.source}>
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <span className="stat-label">Market Regime</span>
            <p className="text-lg font-bold">{report?.regime ?? 'DATA UNAVAILABLE'}</p>
          </div>
          <Pill tone={guardianTag.tone}>{guardianTag.label}</Pill>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <span className="stat-label mb-1.5 block">Supporting Factors</span>
            <ul className="space-y-1">
              {(report?.supportingFactors ?? []).map((factor) => (
                <li key={factor} className="flex items-center gap-2 font-mono text-[10px] text-profit">
                  <span aria-hidden="true">✓</span> {factor}
                </li>
              ))}
              {report && report.supportingFactors.length === 0 ? (
                <li className="font-mono text-[10px] text-ink-muted">No factors recorded.</li>
              ) : null}
            </ul>
          </div>
          <div>
            <span className="stat-label mb-1.5 block">Risk Factors</span>
            <ul className="space-y-1">
              {(report?.riskFactors ?? []).map((factor) => (
                <li key={factor} className="flex items-center gap-2 font-mono text-[10px] text-warning">
                  <span aria-hidden="true">⚠</span> {factor}
                </li>
              ))}
              {report && report.riskFactors.length === 0 ? (
                <li className="font-mono text-[10px] text-ink-muted">No risk factors recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>
        <p className="mt-3 border-t border-border-subtle pt-3 font-mono text-[10px] text-ink-muted">
          Advisory content only — it never carries execution authority.
        </p>
      </DataGate>
    </Card>
  )
}