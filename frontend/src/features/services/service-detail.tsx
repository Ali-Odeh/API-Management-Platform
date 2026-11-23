import { zodResolver } from '@hookform/resolvers/zod'
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'
import { errorMessage } from '@/api/errors'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTable } from '@/components/data-table'
import { Details } from '@/components/details'
import { TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Form } from '@/components/ui/form'
import { Switch } from '@/components/ui/switch'
import { RoutesTable } from '@/features/routes/routes-page'
import { useWorkspace } from '@/features/workspace/context'
import { formatDateTime, formatNumber } from '@/lib/format'
import { showServerError } from '@/lib/forms'
import { useService, useServiceMutations, type Service, type Target } from './queries'
import { EditServiceDialog } from './service-form'

/** `/w/:slug/services/:id`: settings, targets and routes of one service. */
export function ServiceDetailPage() {
  const { slug } = useWorkspace()
  const id = useParams().id ?? ''
  const navigate = useNavigate()
  const service = useService(slug, id)
  const mutations = useServiceMutations(slug)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)

  if (service.isPending) return <LoadingState />
  if (service.error)
    return <ErrorState error={service.error} onRetry={() => void service.refetch()} />
  const s = service.data

  return (
    <>
      <Button asChild variant="link" className="mb-2 px-0">
        <Link to={`/w/${slug}/services`}>
          <ArrowLeft /> Services
        </Link>
      </Button>
      <PageHeader
        title={s.name}
        description={<StatusBadge status={s.is_active ? 'active' : 'inactive'} />}
        actions={
          <>
            <PermissionButton
              permission="services:write"
              variant="outline"
              onClick={() => {
                setEditing(true)
              }}
            >
              <Pencil /> Edit
            </PermissionButton>
            <PermissionButton
              permission="services:write"
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
        <Card>
          <CardHeader>
            <CardTitle>Settings</CardTitle>
          </CardHeader>
          <CardContent>
            <Details
              items={[
                ['Timeout', `${formatNumber(s.timeout_ms)} ms`],
                ['Load balancing', 'Round robin, by weight'],
                [
                  'Active health check',
                  `GET ${s.hc_path} every ${s.hc_interval_s} s; unhealthy after ${s.hc_unhealthy_threshold} failures, healthy again after ${s.hc_healthy_threshold} successes`,
                ],
                [
                  'Passive health check',
                  `A target is taken out for ${s.passive_eject_s} s after ${s.passive_failures} failed requests`,
                ],
                ['Updated', formatDateTime(s.updated_at)],
              ]}
            />
          </CardContent>
        </Card>
        <TargetsCard service={s} />
        <RoutesTable serviceId={s.id} title="Routes to this service" />
      </div>
      <EditServiceDialog
        service={s}
        open={editing}
        onOpenChange={setEditing}
        onSave={(body) => mutations.update.mutateAsync({ id: s.id, body })}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${s.name}?`}
        description="A service that still has routes can't be deleted: delete or move its routes first."
        confirmLabel="Delete service"
        destructive
        onConfirm={async () => {
          await mutations.remove.mutateAsync(s.id)
          toast.success(`Service ${s.name} deleted`)
          void navigate(`/w/${slug}/services`, { replace: true })
        }}
      />
    </>
  )
}

const targetSchema = z.object({
  url: z.string().trim().url('A full URL, like http://orders:9000'),
  weight: z.coerce.number().int().min(1, 'At least 1').max(1000, 'At most 1,000'),
})
type TargetValues = z.input<typeof targetSchema>

/** The service's upstreams: add, change weight, switch on and off, remove. */
function TargetsCard({ service }: { service: Service }) {
  const { slug, can } = useWorkspace()
  const mutations = useServiceMutations(slug)
  const [dialog, setDialog] = useState<Target | 'new' | null>(null)
  const [removing, setRemoving] = useState<Target | null>(null)
  const canWrite = can('services:write') === 'allowed'

  async function toggle(target: Target, on: boolean) {
    try {
      await mutations.updateTarget.mutateAsync({
        id: service.id,
        targetId: target.id,
        body: { is_active: on },
      })
      toast.success(`${target.url} ${on ? 'on' : 'off'}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <section>
      <PageHeader
        level={2}
        title="Targets"
        description="The gateway spreads requests over the active, healthy targets by weight."
        actions={
          <PermissionButton
            permission="services:write"
            variant="outline"
            disabled={service.targets.length >= 50}
            onClick={() => {
              setDialog('new')
            }}
          >
            <Plus /> Add target
          </PermissionButton>
        }
      />
      <DataTable<Target>
        rows={service.targets}
        rowKey={(t) => t.id}
        empty={{ title: 'No targets' }}
        columns={[
          { header: 'URL', cell: (t) => <span className="font-mono text-sm">{t.url}</span> },
          { header: 'Weight', cell: (t) => t.weight },
          {
            header: 'Active',
            cell: (t) => (
              <Switch
                aria-label={`${t.url} active`}
                checked={t.is_active}
                disabled={!canWrite}
                onCheckedChange={(on) => void toggle(t, on)}
              />
            ),
          },
          {
            header: <span className="sr-only">Actions</span>,
            className: 'w-24 text-right',
            cell: (t) => (
              <span className="flex justify-end gap-1">
                <PermissionButton
                  permission="services:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Change weight of ${t.url}`}
                  onClick={() => {
                    setDialog(t)
                  }}
                >
                  <Pencil />
                </PermissionButton>
                <PermissionButton
                  permission="services:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${t.url}`}
                  disabled={service.targets.length === 1}
                  onClick={() => {
                    setRemoving(t)
                  }}
                >
                  <Trash2 />
                </PermissionButton>
              </span>
            ),
          },
        ]}
      />
      {dialog !== null && (
        <TargetDialog
          target={dialog === 'new' ? undefined : dialog}
          onClose={() => {
            setDialog(null)
          }}
          onSave={(values) =>
            dialog === 'new'
              ? mutations.addTarget.mutateAsync({ id: service.id, body: values })
              : mutations.updateTarget.mutateAsync({
                  id: service.id,
                  targetId: dialog.id,
                  body: { weight: values.weight },
                })
          }
        />
      )}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null)
        }}
        title="Remove this target?"
        description={removing?.url}
        confirmLabel="Remove target"
        destructive
        onConfirm={async () => {
          if (!removing) return
          await mutations.removeTarget.mutateAsync({ id: service.id, targetId: removing.id })
          toast.success('Target removed')
        }}
      />
    </section>
  )
}

function TargetDialog({
  target,
  onClose,
  onSave,
}: {
  target?: Target
  onClose: () => void
  onSave: (values: { url: string; weight: number }) => Promise<Target>
}) {
  const form = useForm<TargetValues>({
    resolver: zodResolver(targetSchema),
    defaultValues: { url: target?.url ?? '', weight: target?.weight ?? 100 },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (raw) => {
    setError(null)
    try {
      await onSave(targetSchema.parse(raw))
      toast.success(target ? 'Weight changed' : 'Target added')
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
      title={target ? 'Change weight' : 'Add target'}
      submitLabel={target ? 'Save' : 'Add target'}
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField
          control={form.control}
          name="url"
          label="URL"
          placeholder="http://orders-2:9000"
          disabled={target !== undefined}
        />
        <TextField
          control={form.control}
          name="weight"
          label="Weight"
          type="number"
          description="1 to 1,000. A target with twice the weight gets twice the requests."
        />
      </Form>
    </FormDialog>
  )
}
