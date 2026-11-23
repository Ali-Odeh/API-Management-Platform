import { zodResolver } from '@hookform/resolvers/zod'
import { MoreHorizontal, Plus } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'
import { errorMessage } from '@/api/errors'
import type { WorkspaceStatus } from '@/api/types'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable, Pager } from '@/components/data-table'
import { TextAreaField, TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Form } from '@/components/ui/form'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatDate } from '@/lib/format'
import { showServerError } from '@/lib/forms'
import { LimitsDialog } from './limits-dialog'
import {
  PLATFORM_PAGE_SIZE,
  useAllWorkspaces,
  useWorkspaceLifecycle,
  type PlatformWorkspace,
} from './queries'

type Filter = WorkspaceStatus | 'all'
type Action = 'suspend' | 'unsuspend' | 'delete' | 'restore' | 'limits'

/** `/platform/workspaces`: every workspace and its lifecycle (MT.5). */
export function PlatformWorkspacesPage() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(1)
  const workspaces = useAllWorkspaces(page, filter === 'all' ? undefined : filter)
  const lifecycle = useWorkspaceLifecycle()
  const [creating, setCreating] = useState(false)
  const [acting, setActing] = useState<{ action: Action; ws: PlatformWorkspace } | null>(null)

  async function quick(action: 'unsuspend' | 'restore', ws: PlatformWorkspace) {
    try {
      await (action === 'unsuspend' ? lifecycle.unsuspend : lifecycle.restore).mutateAsync(ws.slug)
      toast.success(`${ws.name} ${action === 'unsuspend' ? 'is active again' : 'restored'}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  const close = () => {
    setActing(null)
  }

  return (
    <>
      <PageHeader
        title="Workspaces"
        description="Every tenant of the platform: create, suspend, delete and restore them, and set their limits."
        actions={
          <Button
            onClick={() => {
              setCreating(true)
            }}
          >
            <Plus /> New workspace
          </Button>
        }
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
          {(['all', 'active', 'suspended', 'deleted', 'purged'] as const).map((s) => (
            <TabsTrigger key={s} value={s} className="capitalize">
              {s}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <DataTable<PlatformWorkspace>
        rows={workspaces.data?.items}
        isPending={workspaces.isPending}
        error={workspaces.error}
        onRetry={() => void workspaces.refetch()}
        rowKey={(w) => w.id}
        empty={{ title: 'No workspaces here' }}
        columns={[
          {
            header: 'Workspace',
            cell: (w) => (
              <span className="flex flex-col">
                <span className="font-medium">{w.name}</span>
                <span className="text-muted-foreground font-mono text-xs">{w.slug}</span>
              </span>
            ),
          },
          {
            header: 'Status',
            cell: (w) => (
              <span className="flex flex-col items-start gap-1">
                <StatusBadge status={w.status} />
                {w.status === 'suspended' && w.status_reason && (
                  <span className="text-muted-foreground text-xs">{w.status_reason}</span>
                )}
                {w.status === 'deleted' && w.purge_after && (
                  <span className="text-muted-foreground text-xs">
                    Restorable until {formatDate(w.purge_after)}
                  </span>
                )}
              </span>
            ),
          },
          { header: 'Created', cell: (w) => formatDate(w.created_at) },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (w) =>
              w.status === 'purged' ? null : (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label={`Actions for ${w.slug}`}>
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {w.status !== 'deleted' && (
                      <>
                        <DropdownMenuItem onSelect={() => void navigate(`/w/${w.slug}`)}>
                          Open dashboard
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => {
                            setActing({ action: 'limits', ws: w })
                          }}
                        >
                          Change limits
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                      </>
                    )}
                    {w.status === 'active' && w.slug !== 'default' && (
                      <DropdownMenuItem
                        onSelect={() => {
                          setActing({ action: 'suspend', ws: w })
                        }}
                      >
                        Suspend
                      </DropdownMenuItem>
                    )}
                    {w.status === 'suspended' && (
                      <DropdownMenuItem onSelect={() => void quick('unsuspend', w)}>
                        Unsuspend
                      </DropdownMenuItem>
                    )}
                    {w.status === 'deleted' && (
                      <DropdownMenuItem onSelect={() => void quick('restore', w)}>
                        Restore
                      </DropdownMenuItem>
                    )}
                    {w.status !== 'deleted' && w.slug !== 'default' && (
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => {
                          setActing({ action: 'delete', ws: w })
                        }}
                      >
                        Delete
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              ),
          },
        ]}
      />
      {workspaces.data && (
        <Pager
          page={page}
          pageSize={PLATFORM_PAGE_SIZE}
          total={workspaces.data.total}
          onPage={setPage}
        />
      )}
      {creating && (
        <CreateWorkspaceDialog
          onClose={() => {
            setCreating(false)
          }}
          onCreate={(body) => lifecycle.create.mutateAsync(body)}
        />
      )}
      {acting?.action === 'suspend' && (
        <SuspendDialog
          workspace={acting.ws}
          onClose={close}
          onSuspend={(reason) => lifecycle.suspend.mutateAsync({ slug: acting.ws.slug, reason })}
        />
      )}
      {acting?.action === 'limits' && (
        <LimitsDialog
          workspace={acting.ws}
          onClose={close}
          onSave={(body) => lifecycle.limits.mutateAsync({ slug: acting.ws.slug, body })}
        />
      )}
      <ConfirmDialog
        open={acting?.action === 'delete'}
        onOpenChange={(open) => {
          if (!open) close()
        }}
        title={`Delete ${acting?.ws.name ?? ''}?`}
        description="Its APIs stop at once and its members lose access. You can restore it for 30 days; then its data is purged for good."
        confirmLabel="Delete workspace"
        destructive
        typeToConfirm={acting?.ws.slug}
        onConfirm={async () => {
          if (!acting) return
          await lifecycle.remove.mutateAsync(acting.ws.slug)
          toast.success(`${acting.ws.name} deleted`)
        }}
      />
    </>
  )
}

const createSchema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(100),
  slug: z
    .string()
    .trim()
    .min(3, 'At least 3 characters')
    .max(50)
    .regex(/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/, 'Lowercase letters, digits and inner dashes'),
  owner_email: z.string().trim().email('Enter a valid email'),
})
type CreateValues = z.infer<typeof createSchema>

function CreateWorkspaceDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (body: CreateValues) => Promise<PlatformWorkspace>
}) {
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { name: '', slug: '', owner_email: '' },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      const created = await onCreate(values)
      toast.success(`Workspace ${created.name} created; ${values.owner_email} is its owner`)
      onClose()
    } catch (e) {
      setError(showServerError(form, e))
    }
  })
  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="New workspace"
      description="A new person gets an invitation email to become its first owner."
      submitLabel="Create workspace"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="Name" placeholder="Acme" />
        <TextField
          control={form.control}
          name="slug"
          label="Slug"
          placeholder="acme"
          description="Its gateway host (acme.localhost) and URLs. It never changes."
        />
        <TextField
          control={form.control}
          name="owner_email"
          label="First owner's email"
          type="email"
        />
      </Form>
    </FormDialog>
  )
}

const suspendSchema = z.object({ reason: z.string().trim().min(1, 'Say why').max(500) })

function SuspendDialog({
  workspace,
  onClose,
  onSuspend,
}: {
  workspace: PlatformWorkspace
  onClose: () => void
  onSuspend: (reason: string) => Promise<PlatformWorkspace>
}) {
  const form = useForm({ resolver: zodResolver(suspendSchema), defaultValues: { reason: '' } })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async ({ reason }) => {
    setError(null)
    try {
      await onSuspend(reason)
      toast.success(`${workspace.name} suspended`)
      onClose()
    } catch (e) {
      setError(showServerError(form, e))
    }
  })
  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={`Suspend ${workspace.name}?`}
      description="Its APIs answer 403 and it becomes read-only for its members until you unsuspend it."
      submitLabel="Suspend"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextAreaField
          control={form.control}
          name="reason"
          label="Reason (members see it)"
          rows={3}
        />
      </Form>
    </FormDialog>
  )
}
