import type { Role } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { ROLE_LABELS } from '@/lib/permissions'
import { cn } from '@/lib/utils'

export function RoleBadge({ role }: { role: Role }) {
  return <Badge variant={role === 'owner' ? 'default' : 'secondary'}>{ROLE_LABELS[role]}</Badge>
}

type Tone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'

const TONE_CLASS: Record<Tone, string> = {
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/40 bg-warning/15 text-foreground',
  danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  neutral: 'border-border bg-muted text-muted-foreground',
  info: 'border-chart-1/30 bg-chart-1/10 text-chart-1',
}

/** Which tone each status of every object type gets. */
const STATUS_TONE: Record<string, Tone> = {
  active: 'success',
  approved: 'success',
  published: 'success',
  healthy: 'success',
  enabled: 'success',
  suspended: 'warning',
  pending: 'warning',
  deprecated: 'warning',
  draft: 'neutral',
  inactive: 'neutral',
  revoked: 'neutral',
  disabled: 'danger',
  blocked: 'danger',
  rejected: 'danger',
  deleted: 'danger',
  purged: 'neutral',
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const tone = STATUS_TONE[status] ?? 'neutral'
  return (
    <Badge variant="outline" className={cn('capitalize', TONE_CLASS[tone], className)}>
      {status}
    </Badge>
  )
}
