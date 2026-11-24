import { Link, useNavigate } from 'react-router'
import { StatusBadge } from '@/components/badges'
import { DataTable } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { formatDate, formatNumber } from '@/lib/format'
import { useMyApps, type App } from './queries'

/** `/portal/apps`: my apps, in every workspace. */
export function PortalAppsPage() {
  const apps = useMyApps()
  const navigate = useNavigate()
  return (
    <>
      <PageHeader
        title="My apps"
        description="Each app has its own keys and the limits of its plan."
      />
      <DataTable<App>
        rows={apps.data}
        isPending={apps.isPending}
        error={apps.error}
        onRetry={() => void apps.refetch()}
        rowKey={(a) => a.id}
        onRowClick={(a) => void navigate(`/portal/apps/${a.id}`)}
        rowLabel={(a) => `Open app ${a.name}`}
        empty={{
          title: 'No apps yet',
          description: 'Open an API in the catalog and create an app for it.',
          action: (
            <Button asChild variant="outline">
              <Link to="/portal">Browse the APIs</Link>
            </Button>
          ),
        }}
        columns={[
          { header: 'App', cell: (a) => <span className="font-medium">{a.name}</span> },
          {
            header: 'API',
            cell: (a) => (
              <span className="flex flex-col">
                <span>{a.api.name}</span>
                <span className="text-muted-foreground text-xs">{a.workspace.name}</span>
              </span>
            ),
          },
          {
            header: 'Plan',
            cell: (a) => `${a.plan.name} · ${formatNumber(a.plan.rate_limit_per_min)}/min`,
          },
          { header: 'Status', cell: (a) => <StatusBadge status={a.status} /> },
          { header: 'Created', cell: (a) => formatDate(a.created_at) },
        ]}
      />
    </>
  )
}
