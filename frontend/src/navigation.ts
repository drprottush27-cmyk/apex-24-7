import type { LucideIcon } from 'lucide-react'
import {
  BookOpen,
  Brain,
  HeartPulse,
  LayoutDashboard,
  LayoutList,
  Radar,
  Shield,
} from 'lucide-react'

export interface NavItem {
  /** react-router path. Root '/' is the home dashboard. */
  path: string
  label: string
  icon: LucideIcon
  /** Included in the mobile bottom navigation bar. */
  primary: boolean
}

export const NAV_ITEMS: NavItem[] = [
  { path: '/', label: 'HOME', icon: LayoutDashboard, primary: true },
  { path: '/scanner', label: 'SCANNER', icon: Radar, primary: true },
  { path: '/positions', label: 'POSITIONS', icon: LayoutList, primary: true },
  { path: '/guardian', label: 'GUARDIAN', icon: Shield, primary: true },
  { path: '/intelligence', label: 'AEGIS', icon: Brain, primary: true },
  { path: '/journal', label: 'JOURNAL', icon: BookOpen, primary: false },
  { path: '/health', label: 'HEALTH', icon: HeartPulse, primary: false },
]

export const PRIMARY_NAV = NAV_ITEMS.filter((item) => item.primary)

export function findNavItem(path: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.path === path)
}