import type { ReactNode } from 'react'

export type PillTone = 'green' | 'red' | 'amber' | 'blue' | 'neutral' | 'dim'

const TONE_CLASS: Record<PillTone, string> = {
  green: 'pill-green',
  red: 'pill-red',
  amber: 'pill-amber',
  blue: 'pill-blue',
  neutral: 'pill-neutral',
  dim: 'pill-dim',
}

interface PillProps {
  tone?: PillTone
  children: ReactNode
  className?: string
  title?: string
}

export function Pill({ tone = 'neutral', children, className = '', title }: PillProps) {
  return (
    <span className={`${TONE_CLASS[tone]} ${className}`} title={title}>
      {children}
    </span>
  )
}