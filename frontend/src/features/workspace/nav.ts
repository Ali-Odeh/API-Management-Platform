import {
  AppWindow,
  BookOpen,
  Boxes,
  CreditCard,
  History,
  LayoutDashboard,
  ScrollText,
  Settings,
  Signpost,
  Users,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import type { Permission } from '@/lib/permissions'

export interface NavItem {
  /** Relative to `/w/:slug`; empty is the overview. */
  path: string
  label: string
  icon: LucideIcon
  /** The read permission the page needs; the item is hidden without it. */
  permission: Permission
}

/** The dashboard sidebar, in order (PLAN.md section 8, plus APIs and Apps). */
export const WORKSPACE_NAV: NavItem[] = [
  { path: '', label: 'Overview', icon: LayoutDashboard, permission: 'usage:read' },
  { path: 'services', label: 'Services', icon: Boxes, permission: 'services:read' },
  { path: 'routes', label: 'Routes', icon: Signpost, permission: 'routes:read' },
  { path: 'consumers', label: 'Consumers', icon: UsersRound, permission: 'consumers:read' },
  { path: 'plans', label: 'Plans', icon: CreditCard, permission: 'plans:read' },
  { path: 'apis', label: 'APIs', icon: BookOpen, permission: 'apis:read' },
  { path: 'apps', label: 'Apps', icon: AppWindow, permission: 'apps:read' },
  { path: 'members', label: 'Members', icon: Users, permission: 'members:read' },
  { path: 'activity', label: 'Activity', icon: History, permission: 'audit:read' },
  { path: 'logs', label: 'Logs', icon: ScrollText, permission: 'usage:read' },
  { path: 'settings', label: 'Settings', icon: Settings, permission: 'workspace:read' },
]
