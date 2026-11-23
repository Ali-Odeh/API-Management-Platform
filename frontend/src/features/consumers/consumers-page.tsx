import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { StatusBadge } from '@/components/badges'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { usePlans } from '@/features/plans/queries'
import { useWorkspace } from '@/features/workspace/context'
import { PAGE_SIZE } from '@/features/workspace/keys'
import { formatDate } from '@/lib/format'
import { ConsumerDialog } from './consumer-form'
import { useConsumerMutations, useConsumers, type Consumer, type ConsumerStatus } from './queries'

type Filter = ConsumerStatus | 'all'

/** `/w/:slug/consumers` */
export function ConsumersPage() {
  const { slug } = useWorkspace()
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState<Filter>('all')
  const consumers = useConsumers(slug, page, filter === 'all' ? undefined : filter)
  const plans = usePlans(slug)
  const { create } = useConsumerMutations(slug)
  const [creating, setCreating] = useState(false)
  const planName = (id: string) => plans.data?.find((p) => p.id === id)?.name ?? '—'

  const add = (
    <PermissionButton
      permission="consumers:write"
      onClick={() => {
        setCreating(true)
      }}
    >
      <Plus /> New consumer
    </PermissionButton>
  )

  return (
    <>
      <PageHeader
        title="Consumers"
        description="Apps and customers that call your APIs with keys."
        actions={add}
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
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="active">Active</TabsTrigger>
          <TabsTrigger value="blocked">Blocked</TabsTrigger>
        </TabsList>
      </Tabs>
      <DataTable<Consumer>
        rows={consumers.data?.items}
        isPending={consumers.isPending}
        error={consumers.error}
        onRetry={() => void consumers.refetch()}
        rowKey={(c) => c.id}
        onRowClick={(c) => void navigate(`/w/${slug}/consumers/${c.id}`)}
        rowLabel={(c) => `Open consumer ${c.name}`}
        empty={{
          title: filter === 'all' ? 'No consumers yet' : `No ${filter} consumers`,
          description:
            filter === 'all'
              ? 'Add a consumer, then give it a key to call your routes.'
              : undefined,
        }}
        columns={[
          { header: 'Name', cell: (c) => <span className="font-medium">{c.name}</span> },
          { header: 'Email', cell: (c) => c.email },
          { header: 'Plan', cell: (c) => planName(c.plan_id) },
          { header: 'Status', cell: (c) => <StatusBadge status={c.status} /> },
          { header: 'Created', cell: (c) => formatDate(c.created_at) },
        ]}
      />
      {consumers.data && (
        <Pager page={page} pageSize={PAGE_SIZE} total={consumers.data.total} onPage={setPage} />
      )}
      {creating && (
        <ConsumerDialog
          plans={plans.data ?? []}
          onClose={() => {
            setCreating(false)
          }}
          onSave={(body) => create.mutateAsync(body)}
        />
      )}
    </>
  )
}
