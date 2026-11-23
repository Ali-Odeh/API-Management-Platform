import { LayoutDashboard, type LucideIcon } from 'lucide-react'
import type { Permission } from '@/lib/permissions'

export interface NavItem {
  /** Relative to `/w/:slug`; empty is the overview. */
  path: string
  label: string
  icon: LucideIcon
  /** The read permission the page needs; the item is hidden without it. */
  permission: Permission
}

/** The dashboard sidebar, in order (PLAN.md section 8). */
export const WORKSPACE_NAV: NavItem[] = [
  { path: '', label: 'Overview', icon: LayoutDashboard, permission: 'workspace:read' },
]
