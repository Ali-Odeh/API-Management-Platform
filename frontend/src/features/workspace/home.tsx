import { StatusBadge } from '@/components/badges'
import { PageHeader } from '@/components/page-header'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatNumber } from '@/lib/format'
import { useWorkspace } from './context'

/** The workspace's landing page until the overview's analytics (FE.2). */
export function WorkspaceHome() {
  const { workspace } = useWorkspace()
  const { usage, limits } = workspace
  const counts = [
    { label: 'Services', used: usage.services, max: limits.max_services },
    { label: 'Routes', used: usage.routes, max: limits.max_routes },
    { label: 'Consumers', used: usage.consumers, max: limits.max_consumers },
  ]
  return (
    <>
      <PageHeader
        title={workspace.name}
        description={
          <span className="flex items-center gap-2">
            <span className="font-mono">{workspace.slug}</span>
            <StatusBadge status={workspace.status} />
          </span>
        }
      />
      <div className="grid gap-4 sm:grid-cols-3">
        {counts.map(({ label, used, max }) => (
          <Card key={label}>
            <CardHeader>
              <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold">{formatNumber(used)}</p>
              <p className="text-muted-foreground text-xs">of {formatNumber(max)} allowed</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  )
}
