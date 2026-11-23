import { useState } from 'react'
import { toast } from 'sonner'
import type { Schema } from '@/api/types'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatDate } from '@/lib/format'
import { PLATFORM_PAGE_SIZE, useDevelopers, useSetDeveloperStatus, type Developer } from './queries'

type Filter = Schema<'DeveloperStatus'> | 'all'

/** `/platform/developers`: who signed up on the portal; block and unblock. */
export function PlatformDevelopersPage() {
  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(1)
  const developers = useDevelopers(page, filter === 'all' ? undefined : filter)
  const setStatus = useSetDeveloperStatus()
  const [changing, setChanging] = useState<Developer | null>(null)
  const blocking = changing?.status === 'active'

  return (
    <>
      <PageHeader
        title="Developers"
        description="People who signed up on the developer portal. Blocking one stops their apps' keys in every workspace."
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
      <DataTable<Developer>
        rows={developers.data?.items}
        isPending={developers.isPending}
        error={developers.error}
        onRetry={() => void developers.refetch()}
        rowKey={(d) => d.id}
        empty={{
          title: 'No developers',
          description: 'Developers sign up on the portal themselves.',
        }}
        columns={[
          { header: 'Email', cell: (d) => <span className="font-medium">{d.email}</span> },
          { header: 'Status', cell: (d) => <StatusBadge status={d.status} /> },
          { header: 'Signed up', cell: (d) => formatDate(d.created_at) },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (d) => (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setChanging(d)
                }}
              >
                {d.status === 'active' ? 'Block' : 'Unblock'}
              </Button>
            ),
          },
        ]}
      />
      {developers.data && (
        <Pager
          page={page}
          pageSize={PLATFORM_PAGE_SIZE}
          total={developers.data.total}
          onPage={setPage}
        />
      )}
      <ConfirmDialog
        open={changing !== null}
        onOpenChange={(open) => {
          if (!open) setChanging(null)
        }}
        title={`${blocking ? 'Block' : 'Unblock'} ${changing?.email ?? ''}?`}
        description={
          blocking
            ? 'The portal refuses them, and their approved apps are blocked in every workspace.'
            : 'They can use the portal again, and their approved apps work again.'
        }
        confirmLabel={blocking ? 'Block' : 'Unblock'}
        destructive={blocking}
        onConfirm={async () => {
          if (!changing) return
          await setStatus.mutateAsync({ id: changing.id, status: blocking ? 'blocked' : 'active' })
          toast.success(`${changing.email} ${blocking ? 'blocked' : 'unblocked'}`)
        }}
      />
    </>
  )
}
