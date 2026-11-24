import { ArrowLeft, Ban, CheckCircle2, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { errorMessage } from '@/api/errors'
import { StatusBadge } from '@/components/badges'
import { RangeSelect, TrafficChart } from '@/components/charts'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable } from '@/components/data-table'
import { Details } from '@/components/details'
import { PageHeader } from '@/components/page-header'
import { ShownOnceDialog } from '@/components/shown-once-dialog'
import { PermissionButton } from '@/components/permission-button'
import { ErrorState, LoadingState, Spinner } from '@/components/states'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useTraffic } from '@/features/analytics/queries'
import { planLabel, usePlans } from '@/features/plans/queries'
import { useWorkspace } from '@/features/workspace/context'
import { formatDate, formatRelative } from '@/lib/format'
import type { RangeKey } from '@/lib/time-range'
import { ConsumerDialog } from './consumer-form'
import {
  useConsumer,
  useConsumerMutations,
  useKeyMutations,
  useKeys,
  type Consumer,
  type Key,
  type KeyCreated,
} from './queries'

/** `/w/:slug/consumers/:id`: the consumer, its keys and its traffic. */
export function ConsumerDetailPage() {
  const { slug } = useWorkspace()
  const id = useParams().id ?? ''
  const navigate = useNavigate()
  const consumer = useConsumer(slug, id)
  const plans = usePlans(slug)
  const mutations = useConsumerMutations(slug)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [range, setRange] = useState<RangeKey>('24h')
  const traffic = useTraffic(slug, range, { consumer_id: id })

  if (consumer.isPending) return <LoadingState />
  if (consumer.error) {
    return <ErrorState error={consumer.error} onRetry={() => void consumer.refetch()} />
  }
  const c = consumer.data
  const plan = plans.data?.find((p) => p.id === c.plan_id)
  const blocked = c.status === 'blocked'

  async function setStatus(status: Consumer['status']) {
    try {
      await mutations.update.mutateAsync({ id: c.id, body: { status } })
      toast.success(status === 'blocked' ? `${c.name} blocked` : `${c.name} unblocked`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <>
      <Button asChild variant="link" className="mb-2 px-0">
        <Link to={`/w/${slug}/consumers`}>
          <ArrowLeft /> Consumers
        </Link>
      </Button>
      <PageHeader
        title={c.name}
        description={<StatusBadge status={c.status} />}
        actions={
          <>
            <PermissionButton
              permission="consumers:write"
              variant="outline"
              onClick={() => void setStatus(blocked ? 'active' : 'blocked')}
            >
              {blocked ? <CheckCircle2 /> : <Ban />}
              {blocked ? 'Unblock' : 'Block'}
            </PermissionButton>
            <PermissionButton
              permission="consumers:write"
              variant="outline"
              onClick={() => {
                setEditing(true)
              }}
            >
              <Pencil /> Edit
            </PermissionButton>
            <PermissionButton
              permission="consumers:write"
              variant="outline"
              onClick={() => {
                setDeleting(true)
              }}
            >
              <Trash2 /> Delete
            </PermissionButton>
          </>
        }
      />
      <div className="grid gap-6">
        {blocked && (
          <Alert variant="destructive">
            <Ban />
            <AlertTitle>Blocked</AlertTitle>
            <AlertDescription>The gateway answers its keys with 403.</AlertDescription>
          </Alert>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent>
            <Details
              items={[
                ['Email', c.email],
                ['Plan', plan ? planLabel(plan) : '—'],
                ['Created', formatDate(c.created_at)],
              ]}
            />
          </CardContent>
        </Card>
        <KeysSection consumerId={c.id} />
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Traffic</CardTitle>
            <RangeSelect value={range} onChange={setRange} />
          </CardHeader>
          <CardContent>
            {traffic.data ? (
              <TrafficChart traffic={traffic.data} />
            ) : traffic.error ? (
              <ErrorState error={traffic.error} />
            ) : (
              <LoadingState />
            )}
          </CardContent>
        </Card>
      </div>
      {editing && (
        <ConsumerDialog
          consumer={c}
          plans={plans.data ?? []}
          onClose={() => {
            setEditing(false)
          }}
          onSave={(body) => mutations.update.mutateAsync({ id: c.id, body })}
        />
      )}
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${c.name}?`}
        description="Its keys stop working at once. This can't be undone."
        confirmLabel="Delete consumer"
        destructive
        onConfirm={async () => {
          await mutations.remove.mutateAsync(c.id)
          toast.success(`Consumer ${c.name} deleted`)
          void navigate(`/w/${slug}/consumers`, { replace: true })
        }}
      />
    </>
  )
}

function KeysSection({ consumerId }: { consumerId: string }) {
  const { slug } = useWorkspace()
  const keys = useKeys(slug, consumerId)
  const { create, revoke } = useKeyMutations(slug, consumerId)
  const [naming, setNaming] = useState(false)
  const [created, setCreated] = useState<KeyCreated | null>(null)
  const [revoking, setRevoking] = useState<Key | null>(null)

  return (
    <section>
      <PageHeader
        level={2}
        title="API keys"
        description="Callers send a key in the X-API-Key header. At most 10 active keys."
        actions={
          <PermissionButton
            permission="keys:write"
            variant="outline"
            onClick={() => {
              setNaming(true)
            }}
          >
            <Plus /> Generate key
          </PermissionButton>
        }
      />
      <DataTable<Key>
        rows={keys.data}
        isPending={keys.isPending}
        error={keys.error}
        onRetry={() => void keys.refetch()}
        rowKey={(k) => k.id}
        empty={{
          title: 'No keys',
          description: 'Generate a key so this consumer can call your routes.',
        }}
        columns={[
          { header: 'Name', cell: (k) => k.name },
          {
            header: 'Key',
            cell: (k) => <code className="font-mono text-xs">{k.key_prefix}…</code>,
          },
          { header: 'Created', cell: (k) => formatDate(k.created_at) },
          { header: 'Last used', cell: (k) => formatRelative(k.last_used_at) },
          {
            header: 'Status',
            cell: (k) => <StatusBadge status={k.revoked_at ? 'revoked' : 'active'} />,
          },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'text-right',
            cell: (k) =>
              k.revoked_at ? null : (
                <PermissionButton
                  permission="keys:write"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setRevoking(k)
                  }}
                >
                  Revoke
                </PermissionButton>
              ),
          },
        ]}
      />
      <NewKeyDialog
        open={naming}
        onOpenChange={setNaming}
        onCreate={async (name) => {
          const key = await create.mutateAsync(name)
          setNaming(false)
          setCreated(key)
        }}
      />
      <ShownOnceDialog
        created={created}
        onClose={() => {
          setCreated(null)
        }}
      />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open) setRevoking(null)
        }}
        title={`Revoke ${revoking?.name ?? ''}?`}
        description="Calls with this key fail from now on. A revoked key can't be restored."
        confirmLabel="Revoke key"
        destructive
        onConfirm={async () => {
          if (!revoking) return
          await revoke.mutateAsync(revoking.id)
          toast.success(`Key ${revoking.name} revoked`)
        }}
      />
    </section>
  )
}

function NewKeyDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (name: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function submit() {
    setPending(true)
    setError(null)
    try {
      await onCreate(name.trim())
      setName('')
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Generate a key</DialogTitle>
          <DialogDescription>Name it after where it will be used.</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <Label htmlFor="key-name">Name</Label>
          <Input
            id="key-name"
            value={name}
            maxLength={100}
            placeholder="production"
            onChange={(e) => {
              setName(e.target.value)
            }}
          />
          {error && (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          )}
          <DialogFooter className="mt-2">
            <Button type="submit" disabled={pending || !name.trim()}>
              {pending && <Spinner />} Generate
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
