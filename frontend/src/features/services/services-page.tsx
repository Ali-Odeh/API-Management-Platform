import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { StatusBadge } from '@/components/badges'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { useWorkspace } from '@/features/workspace/context'
import { PAGE_SIZE } from '@/features/workspace/keys'
import { formatNumber } from '@/lib/format'
import { useServiceMutations, useServices, type Service } from './queries'
import { CreateServiceDialog } from './service-form'

/** `/w/:slug/services` */
export function ServicesPage() {
  const { slug } = useWorkspace()
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const services = useServices(slug, page)
  const { create } = useServiceMutations(slug)
  const [creating, setCreating] = useState(false)

  const add = (
    <PermissionButton
      permission="services:write"
      onClick={() => {
        setCreating(true)
      }}
    >
      <Plus /> New service
    </PermissionButton>
  )

  return (
    <>
      <PageHeader
        title="Services"
        description="Backends the gateway forwards to, each with one or more targets."
        actions={add}
      />
      <DataTable<Service>
        rows={services.data?.items}
        isPending={services.isPending}
        error={services.error}
        onRetry={() => void services.refetch()}
        rowKey={(s) => s.id}
        onRowClick={(s) => void navigate(`/w/${slug}/services/${s.id}`)}
        rowLabel={(s) => `Open service ${s.name}`}
        empty={{
          title: 'No services yet',
          description: 'Add the first backend the gateway should forward requests to.',
          action: add,
        }}
        columns={[
          { header: 'Name', cell: (s) => <span className="font-medium">{s.name}</span> },
          {
            header: 'Targets',
            cell: (s) => (
              <span className="text-muted-foreground font-mono text-xs">
                {s.targets[0]?.url}
                {s.targets.length > 1 && ` +${s.targets.length - 1}`}
              </span>
            ),
          },
          { header: 'Timeout', cell: (s) => `${formatNumber(s.timeout_ms)} ms` },
          {
            header: 'Status',
            cell: (s) => <StatusBadge status={s.is_active ? 'active' : 'inactive'} />,
          },
        ]}
      />
      {services.data && (
        <Pager page={page} pageSize={PAGE_SIZE} total={services.data.total} onPage={setPage} />
      )}
      <CreateServiceDialog
        open={creating}
        onOpenChange={setCreating}
        onCreate={(body) => create.mutateAsync(body)}
      />
    </>
  )
}
