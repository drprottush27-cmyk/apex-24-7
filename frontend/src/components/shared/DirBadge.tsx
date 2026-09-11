import type { PositionSide } from '../../types'
import { Pill } from './Pill'

interface DirBadgeProps {
  side: PositionSide
}

export function DirBadge({ side }: DirBadgeProps) {
  if (side === 'LONG') {
    return <Pill tone="green">LONG</Pill>
  }
  return <Pill tone="red">SHORT</Pill>
}