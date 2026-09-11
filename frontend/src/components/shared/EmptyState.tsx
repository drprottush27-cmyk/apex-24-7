import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description?: ReactNode
}

export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-start gap-1 rounded-md border border-border-subtle bg-surface-2 px-4 py-6">
      <p className="font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-ink-dim">{title}</p>
      {description ? (
        <p className="font-mono text-[10px] leading-relaxed text-ink-muted">{description}</p>
      ) : null}
    </div>
  )
}