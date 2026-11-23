import type { LucideIcon } from 'lucide-react'
import { NavLink } from 'react-router'
import { cn } from '@/lib/utils'

export interface SidebarLink {
  to: string
  label: string
  icon: LucideIcon
  /** Only the exact path is active (for the overview / index page). */
  end?: boolean
}

export function SidebarNav({
  title,
  links,
  onNavigate,
}: {
  title?: string
  links: SidebarLink[]
  onNavigate?: () => void
}) {
  return (
    <nav aria-label={title ?? 'Sections'} className="grid gap-1 p-3">
      {title && (
        <p className="text-muted-foreground px-2 pb-1 text-xs font-medium uppercase tracking-wide">
          {title}
        </p>
      )}
      {links.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
              'hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
              isActive
                ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                : 'text-muted-foreground',
            )
          }
        >
          <Icon aria-hidden className="size-4" />
          {label}
        </NavLink>
      ))}
    </nav>
  )
}
