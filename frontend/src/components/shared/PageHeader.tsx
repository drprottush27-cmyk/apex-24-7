import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'

interface PageHeaderProps {
  eyebrow: string
  title: string
  description?: string
  right?: ReactNode
}

export function PageHeader({ eyebrow, title, description, right }: PageHeaderProps) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="mb-1 flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-accent">
          {eyebrow} <ChevronRight className="h-3 w-3" aria-hidden="true" />
        </p>
        <h1 className="text-xl font-bold tracking-[0.02em]">{title}</h1>
        {description ? <p className="mt-1 font-mono text-[10px] text-ink-muted">{description}</p> : null}
      </div>
      {right ? <div className="flex items-center gap-2">{right}</div> : null}
    </div>
  )
}