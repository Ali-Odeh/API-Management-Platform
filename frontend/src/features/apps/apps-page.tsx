import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import type { Schema } from '@/api/types'
import { StatusBadge } from '@/components/badges'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useWorkspace } from '@/features/workspace/context'
import { PAGE_SIZE, wsKey } from '@/features/workspace/keys'
import { formatDate, formatNumber } from '@/lib/format'

type App = Schema<'AppAdminOut'>
type Filter = Schema<'AppStatus'> | 'all'

/**
 * `/w/:slug/apps`: developers' apps from the portal. Apps on a plan that
 * needs approval wait here; approving opens their keys, rejecting (also
 * later) takes access away.
 */
export function AppsPage() {
  const { slug } = useWorkspace()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<Filter>('pending')
  const [page, setPage] = useState(1)
  const apps = useQuery({
    queryKey: wsKey(slug, 'apps', filter, page),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/apps', {
          params: {
            path: { slug },
            query: { page, page_size: PAGE_SIZE, status: filter === 'all' ? undefined : filter },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })
  const decide = useMutation({
    mutationFn: ({ id, status }: { id: string; status: Schema<'Decision'> }) =>
      unwrap(
        api.PATCH('/api/v1/workspaces/{slug}/apps/{app_id}', {
          params: { path: { slug, app_id: id } },
          body: { status },
        }),
      ),
    // A decision also blocks or opens the app's consumer.
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: wsKey(slug, 'apps') }),
        queryClient.invalidateQueries({ queryKey: wsKey(slug, 'consumers') }),
      ]),
  })

  async function run(app: App, status: Schema<'Decision'>) {
    try {
      await decide.mutateAsync({ id: app.id, status })
      toast.success(`${app.name} ${status}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <>
      <PageHeader
        title="Apps"
        description="Apps developers made on the portal for your APIs, and the approval queue."
      />
      <Tabs
        value={filter}
        onValueChange={(v) => {
          setFilter(v as Filter)
          setPage(1)
        }}
        className="mb-4"
      >
        <TabsList>
          <TabsTrigger value="pending">Waiting</TabsTrigger>
          <TabsTrigger value="approved">Approved</TabsTrigger>
          <TabsTrigger value="rejected">Rejected</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>
      <DataTable<App>
        rows={apps.data?.items}
        isPending={apps.isPending}
        error={apps.error}
        onRetry={() => void apps.refetch()}
        rowKey={(a) => a.id}
        empty={{
          title: filter === 'pending' ? 'Nothing is waiting' : 'No apps',
          description:
            filter === 'pending'
              ? 'Apps on plans that need approval appear here.'
              : 'Developers create apps on the portal for published APIs.',
        }}
        columns={[
          {
            header: 'App',
            cell: (a) => (
              <span className="flex flex-col">
                <span className="font-medium">{a.name}</span>
                <span className="text-muted-foreground text-xs">{a.developer_email}</span>
              </span>
            ),
          },
          { header: 'API', cell: (a) => a.api.name },
          {
            header: 'Plan',
            cell: (a) => `${a.plan.name} · ${formatNumber(a.plan.rate_limit_per_min)}/min`,
          },
          { header: 'Status', cell: (a) => <StatusBadge status={a.status} /> },
          { header: 'Created', cell: (a) => formatDate(a.created_at) },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (a) => (
              <span className="flex justify-end gap-1">
                {a.status !== 'approved' && (
                  <PermissionButton
                    permission="apps:write"
                    size="sm"
                    variant="outline"
                    onClick={() => void run(a, 'approved')}
                  >
                    <Check /> Approve
                  </PermissionButton>
                )}
                {a.status !== 'rejected' && (
                  <PermissionButton
                    permission="apps:write"
                    size="sm"
                    variant="ghost"
                    onClick={() => void run(a, 'rejected')}
                  >
                    <X /> Reject
                  </PermissionButton>
                )}
              </span>
            ),
          },
        ]}
      />
      {apps.data && (
        <Pager page={page} pageSize={PAGE_SIZE} total={apps.data.total} onPage={setPage} />
      )}
    </>
  )
}
