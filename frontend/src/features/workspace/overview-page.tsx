import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { api, unwrap } from '@/api/client'
import { StatusBadge } from '@/components/badges'
import { RangeSelect, TrafficChart } from '@/components/charts'
import { errorRate, formatMs } from '@/lib/metrics'
import { DataTable } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { ErrorState, LoadingState } from '@/components/states'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useTopConsumers, useTopRoutes, useTraffic } from '@/features/analytics/queries'
import { formatNumber, formatRelative } from '@/lib/format'
import { statusClass } from '@/lib/http'
import type { RangeKey } from '@/lib/time-range'
import { cn } from '@/lib/utils'
import { useWorkspace } from './context'
import { wsKey } from './keys'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="gap-2">
      <CardHeader>
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value}</p>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </CardContent>
    </Card>
  )
}

/** `/w/:slug`: traffic, errors and latency at a glance, plus usage of the quotas. */
export function OverviewPage() {
  const { slug, workspace } = useWorkspace()
  const [range, setRange] = useState<RangeKey>('24h')
  const traffic = useTraffic(slug, range)
  const topRoutes = useTopRoutes(slug, range)
  const topConsumers = useTopConsumers(slug, range, 100)
  const recent = useQuery({
    queryKey: wsKey(slug, 'logs', 'recent'),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/request-logs', {
          params: { path: { slug }, query: { page: 1, page_size: 100 } },
        }),
      ),
  })
  const recentErrors = recent.data?.items.filter((l) => l.status >= 400).slice(0, 5)
  const totals = traffic.data?.totals
  const activeConsumers = topConsumers.data?.items.filter((c) => c.consumer_id).length

  return (
    <>
      <PageHeader
        title={workspace.name}
        description={
          <span className="flex items-center gap-2">
            <span className="font-mono">{slug}</span>
            <StatusBadge status={workspace.status} />
          </span>
        }
        actions={<RangeSelect value={range} onChange={setRange} />}
      />
      <div className="grid gap-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Requests" value={totals ? formatNumber(totals.requests) : '—'} />
          <Stat
            label="Error rate"
            value={totals ? errorRate(totals) : '—'}
            hint={totals ? `${formatNumber(totals.server_errors)} server errors` : undefined}
          />
          <Stat
            label="Latency p95"
            value={formatMs(totals?.latency.p95_ms)}
            hint={totals ? `p50 ${formatMs(totals.latency.p50_ms)}` : undefined}
          />
          <Stat
            label="Active consumers"
            value={activeConsumers === undefined ? '—' : formatNumber(activeConsumers)}
            hint="with requests in this period"
          />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Traffic</CardTitle>
          </CardHeader>
          <CardContent>
            {traffic.data ? (
              <TrafficChart traffic={traffic.data} />
            ) : traffic.error ? (
              <ErrorState error={traffic.error} onRetry={() => void traffic.refetch()} />
            ) : (
              <LoadingState />
            )}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <section>
            <h2 className="mb-3 text-lg font-semibold">Busiest routes</h2>
            <DataTable
              rows={topRoutes.data?.items}
              isPending={topRoutes.isPending}
              error={topRoutes.error}
              rowKey={(r) => r.route_id ?? 'none'}
              empty={{ title: 'No traffic' }}
              columns={[
                {
                  header: 'Route',
                  cell: (r) => (
                    <span className="font-mono text-xs">{r.path_prefix ?? 'no route (404)'}</span>
                  ),
                },
                {
                  header: 'Requests',
                  cell: (r) => formatNumber(r.requests),
                  className: 'text-right',
                },
                { header: 'Errors', cell: (r) => errorRate(r), className: 'text-right' },
                { header: 'p95', cell: (r) => formatMs(r.latency.p95_ms), className: 'text-right' },
              ]}
            />
          </section>
          <section>
            <h2 className="mb-3 text-lg font-semibold">Busiest consumers</h2>
            <DataTable
              rows={topConsumers.data?.items.slice(0, 5)}
              isPending={topConsumers.isPending}
              error={topConsumers.error}
              rowKey={(c) => c.consumer_id ?? 'none'}
              empty={{ title: 'No traffic' }}
              columns={[
                {
                  header: 'Consumer',
                  cell: (c) =>
                    c.consumer_id && c.name ? (
                      <Link
                        className="hover:underline"
                        to={`/w/${slug}/consumers/${c.consumer_id}`}
                      >
                        {c.name}
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">no key (public or refused)</span>
                    ),
                },
                {
                  header: 'Requests',
                  cell: (c) => formatNumber(c.requests),
                  className: 'text-right',
                },
                { header: 'Errors', cell: (c) => errorRate(c), className: 'text-right' },
              ]}
            />
          </section>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <section>
            <h2 className="mb-3 text-lg font-semibold">Recent errors</h2>
            <DataTable
              rows={recentErrors}
              isPending={recent.isPending}
              error={recent.error}
              rowKey={(l) => l.event_id}
              empty={{ title: 'No errors lately' }}
              columns={[
                {
                  header: 'Status',
                  cell: (l) => (
                    <span className={cn('font-mono font-semibold', statusClass(l.status))}>
                      {l.status}
                    </span>
                  ),
                },
                {
                  header: 'Request',
                  cell: (l) => `${l.method} ${l.path}`,
                  className: 'max-w-64 truncate font-mono text-xs',
                },
                { header: 'When', cell: (l) => formatRelative(l.ts) },
              ]}
            />
            <Link
              to={`/w/${slug}/logs`}
              className="text-muted-foreground mt-2 inline-block text-sm hover:underline"
            >
              All logs →
            </Link>
          </section>
          <section>
            <h2 className="mb-3 text-lg font-semibold">Quotas</h2>
            <Card>
              <CardContent className="grid gap-3">
                {(
                  [
                    ['Services', workspace.usage.services, workspace.limits.max_services],
                    ['Routes', workspace.usage.routes, workspace.limits.max_routes],
                    ['Consumers', workspace.usage.consumers, workspace.limits.max_consumers],
                  ] as const
                ).map(([label, used, max]) => (
                  <div key={label} className="grid gap-1">
                    <div className="flex justify-between text-sm">
                      <span>{label}</span>
                      <span className="text-muted-foreground">
                        {formatNumber(used)} of {formatNumber(max)}
                      </span>
                    </div>
                    <div className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden>
                      <div
                        className="bg-chart-1 h-full"
                        style={{ width: `${Math.min(100, (used / max) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </section>
        </div>
      </div>
    </>
  )
}
