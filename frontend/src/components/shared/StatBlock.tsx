import type { ReactNode } from 'react'

interface StatBlockProps {
  label: string
  value: ReactNode
  tone?: 'default' | 'green' | 'red' | 'amber' | 'muted'
  title?: string
}

const TONE_CLASS: Record<NonNullable<StatBlockProps['tone']>, string> = {
  default: 'text-ink',
  green: 'text-profit',
  red: 'text-loss',
  amber: 'text-warning',
  muted: 'text-ink-muted',
}

export function StatBlock({ label, value, tone = 'default', title }: StatBlockProps) {
  return (
    <div>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${TONE_CLASS[tone]}`} title={title}>
        {value}
      </span>
    </div>
  )
}