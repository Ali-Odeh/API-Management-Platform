import { Search } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import { RoleBadge, StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable, Pager } from '@/components/data-table'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMe } from '@/features/me/queries'
import { formatDate } from '@/lib/format'
import { PLATFORM_PAGE_SIZE, useSetUserActive, useUsers, type PlatformUser } from './queries'

type Filter = 'all' | 'active' | 'disabled'

/** `/platform/users`: everyone with an account, their workspaces; disable and enable. */
export function PlatformUsersPage() {
  const { data: me } = useMe()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [email, setEmail] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const users = useUsers(page, {
    is_active: filter === 'all' ? undefined : filter === 'active',
    email,
  })
  const setActive = useSetUserActive()
  const [changing, setChanging] = useState<PlatformUser | null>(null)

  return (
    <>
      <PageHeader
        title="Users"
        description="Everyone who can sign in to the dashboard. Disabling someone locks them out at once, everywhere."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          value={filter}
          onValueChange={(v) => {
            setFilter(v as Filter)
            setPage(1)
          }}
        >
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="active">Active</TabsTrigger>
            <TabsTrigger value="disabled">Disabled</TabsTrigger>
          </TabsList>
        </Tabs>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setEmail(search.trim() || undefined)
            setPage(1)
          }}
        >
          <Input
            aria-label="Search by email"
            placeholder="Search by email"
            className="h-9 w-64"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
            }}
          />
          <Button type="submit" variant="outline" size="icon" aria-label="Search">
            <Search />
          </Button>
        </form>
      </div>
      <DataTable<PlatformUser>
        rows={users.data?.items}
        isPending={users.isPending}
        error={users.error}
        onRetry={() => void users.refetch()}
        rowKey={(u) => u.id}
        empty={{ title: 'Nobody matches' }}
        columns={[
          {
            header: 'Email',
            cell: (u) => (
              <span className="flex items-center gap-2 font-medium">
                {u.email}
                {u.is_platform_admin && <Badge>Platform Admin</Badge>}
              </span>
            ),
          },
          {
            header: 'Workspaces',
            cell: (u) =>
              u.workspaces.length === 0 ? (
                <span className="text-muted-foreground">none</span>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {u.workspaces.map((w) => (
                    <li key={w.slug} className="flex items-center gap-1 text-sm">
                      {w.status === 'active' || w.status === 'suspended' ? (
                        <Link to={`/w/${w.slug}`} className="hover:underline">
                          {w.name}
                        </Link>
                      ) : (
                        <span className="text-muted-foreground line-through">{w.name}</span>
                      )}
                      <RoleBadge role={w.role} />
                    </li>
                  ))}
                </ul>
              ),
          },
          {
            header: 'Status',
            cell: (u) => <StatusBadge status={u.is_active ? 'active' : 'disabled'} />,
          },
          { header: 'Since', cell: (u) => formatDate(u.created_at) },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (u) =>
              u.id === me?.id ? null : (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setChanging(u)
                  }}
                >
                  {u.is_active ? 'Disable' : 'Enable'}
                </Button>
              ),
          },
        ]}
      />
      {users.data && (
        <Pager
          page={page}
          pageSize={PLATFORM_PAGE_SIZE}
          total={users.data.total}
          onPage={setPage}
        />
      )}
      <ConfirmDialog
        open={changing !== null}
        onOpenChange={(open) => {
          if (!open) setChanging(null)
        }}
        title={`${changing?.is_active ? 'Disable' : 'Enable'} ${changing?.email ?? ''}?`}
        description={
          changing?.is_active
            ? 'Their sessions stop working at once and they cannot sign in. A workspace where they were the only owner then needs a new owner.'
            : 'They can sign in again with their roles as before.'
        }
        confirmLabel={changing?.is_active ? 'Disable' : 'Enable'}
        destructive={changing?.is_active}
        onConfirm={async () => {
          if (!changing) return
          await setActive.mutateAsync({ id: changing.id, isActive: !changing.is_active })
          toast.success(`${changing.email} ${changing.is_active ? 'disabled' : 'enabled'}`)
        }}
      />
    </>
  )
}
