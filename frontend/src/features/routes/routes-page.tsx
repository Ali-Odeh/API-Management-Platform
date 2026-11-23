import { Lock, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAllServices } from '@/features/services/queries'
import { useWorkspace } from '@/features/workspace/context'
import { PAGE_SIZE } from '@/features/workspace/keys'
import { useRouteMutations, useRoutes, type Route } from './queries'
import { RouteDialog } from './route-form'

const ALL = 'all'

/**
 * A page of routes with add, edit and delete. Used on the Routes page and
 * on a service's page (`serviceId` fixed).
 */
export function RoutesTable({
  serviceId,
  filterable = false,
  title,
}: {
  serviceId?: string
  filterable?: boolean
  title?: React.ReactNode
}) {
  const { slug } = useWorkspace()
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState(ALL)
  const service = serviceId ?? (filter === ALL ? undefined : filter)
  const routes = useRoutes(slug, page, service)
  const services = useAllServices(slug)
  const { create, update, remove } = useRouteMutations(slug)
  const [editing, setEditing] = useState<Route | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Route | null>(null)

  const serviceName = (id: string) => services.data?.find((s) => s.id === id)?.name ?? '—'
  const add = (
    <PermissionButton
      permission="routes:write"
      disabled={services.data?.length === 0}
      onClick={() => {
        setEditing('new')
      }}
    >
      <Plus /> New route
    </PermissionButton>
  )

  return (
    <>
      <PageHeader
        level={serviceId ? 2 : 1}
        title={title ?? 'Routes'}
        description={
          serviceId
            ? undefined
            : 'Paths the gateway answers, the service each one goes to, and its rules.'
        }
        actions={
          <>
            {filterable && (
              <Select
                value={filter}
                onValueChange={(v) => {
                  setFilter(v)
                  setPage(1)
                }}
              >
                <SelectTrigger className="w-44" aria-label="Filter by service">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All services</SelectItem>
                  {services.data?.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {add}
          </>
        }
      />
      <DataTable<Route>
        rows={routes.data?.items}
        isPending={routes.isPending}
        error={routes.error}
        onRetry={() => void routes.refetch()}
        rowKey={(r) => r.id}
        empty={{
          title: 'No routes',
          description:
            services.data?.length === 0
              ? 'Add a service first: every route forwards to one.'
              : 'Add a path the gateway should answer.',
        }}
        columns={[
          {
            header: 'Path',
            cell: (r) => (
              <span className="flex items-center gap-1.5 font-mono text-sm">
                {r.auth_required && <Lock aria-label="API key required" className="size-3.5" />}
                {r.path_prefix}
              </span>
            ),
          },
          {
            header: 'Methods',
            cell: (r) => (
              <span className="flex flex-wrap gap-1">
                {r.methods.map((m) => (
                  <Badge key={m} variant="secondary" className="font-mono text-[10px]">
                    {m}
                  </Badge>
                ))}
              </span>
            ),
          },
          ...(serviceId
            ? []
            : [{ header: 'Service', cell: (r: Route) => serviceName(r.service_id) }]),
          {
            header: 'CORS',
            cell: (r) => (r.cors.origins?.length ? r.cors.origins.join(', ') : 'off'),
            className: 'max-w-40 truncate text-xs',
          },
          {
            header: 'Status',
            cell: (r) => <StatusBadge status={r.is_active ? 'active' : 'inactive'} />,
          },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'w-24 text-right',
            cell: (r) => (
              <span className="flex justify-end gap-1">
                <PermissionButton
                  permission="routes:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Edit ${r.path_prefix}`}
                  onClick={() => {
                    setEditing(r)
                  }}
                >
                  <Pencil />
                </PermissionButton>
                <PermissionButton
                  permission="routes:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${r.path_prefix}`}
                  onClick={() => {
                    setDeleting(r)
                  }}
                >
                  <Trash2 />
                </PermissionButton>
              </span>
            ),
          },
        ]}
      />
      {routes.data && (
        <Pager page={page} pageSize={PAGE_SIZE} total={routes.data.total} onPage={setPage} />
      )}
      {editing !== null && (
        <RouteDialog
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null)
          }}
          route={editing === 'new' ? undefined : editing}
          services={services.data ?? []}
          serviceId={serviceId}
          onSave={(body) =>
            editing === 'new'
              ? create.mutateAsync({ ...body, max_body_bytes: body.max_body_bytes ?? undefined })
              : update.mutateAsync({ id: editing.id, body })
          }
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        title={`Delete ${deleting?.path_prefix ?? ''}?`}
        description="The gateway stops answering this path. An API that lists it loses it."
        confirmLabel="Delete route"
        destructive
        onConfirm={async () => {
          if (!deleting) return
          await remove.mutateAsync(deleting.id)
          toast.success(`Route ${deleting.path_prefix} deleted`)
        }}
      />
    </>
  )
}

/** `/w/:slug/routes` */
export function RoutesPage() {
  return <RoutesTable filterable />
}
