import type { ReactNode } from 'react'

interface SectionHeaderProps {
  title: string
  children?: ReactNode
  action?: ReactNode
}

export function SectionHeader({ title, children, action }: SectionHeaderProps) {
  return (
    <div className="section-header">
      <span className="section-label">{title}</span>
      <div className="flex items-center gap-3">
        {children}
        {action}
      </div>
    </div>
  )
}