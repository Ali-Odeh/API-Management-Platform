import { zodResolver } from '@hookform/resolvers/zod'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { SwitchField, TextField } from '@/components/fields'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Form } from '@/components/ui/form'
import { useWorkspace } from '@/features/workspace/context'
import { formatNumber } from '@/lib/format'
import { showServerError } from '@/lib/forms'
import { usePlanMutations, usePlans, type Plan, type PlanCreate } from './queries'

/** `/w/:slug/plans`: rate limits and quotas consumers are put on. */
export function PlansPage() {
  const { slug, workspace } = useWorkspace()
  const plans = usePlans(slug)
  const mutations = usePlanMutations(slug)
  const [editing, setEditing] = useState<Plan | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Plan | null>(null)

  return (
    <>
      <PageHeader
        title="Plans"
        description={`Each consumer is on one plan. A plan's rate limit can't be above the workspace's ${formatNumber(workspace.limits.rate_limit_per_min)} requests per minute.`}
        actions={
          <PermissionButton
            permission="plans:write"
            onClick={() => {
              setEditing('new')
            }}
          >
            <Plus /> New plan
          </PermissionButton>
        }
      />
      {plans.isPending ? (
        <LoadingState />
      ) : plans.error ? (
        <ErrorState error={plans.error} onRetry={() => void plans.refetch()} />
      ) : plans.data.length === 0 ? (
        <EmptyState title="No plans" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {plans.data.map((plan) => (
            <Card key={plan.id}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2">
                  <span className="truncate">{plan.name}</span>
                  {plan.is_default && <Badge>Default</Badge>}
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-1 text-sm">
                <p>
                  <span className="text-2xl font-semibold">
                    {formatNumber(plan.rate_limit_per_min)}
                  </span>{' '}
                  <span className="text-muted-foreground">requests / minute</span>
                </p>
                <p className="text-muted-foreground">
                  {plan.monthly_quota
                    ? `${formatNumber(plan.monthly_quota)} requests a month`
                    : 'No monthly quota'}
                </p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {plan.portal_visible && <Badge variant="outline">On the portal</Badge>}
                  {plan.requires_approval && <Badge variant="outline">Needs approval</Badge>}
                </div>
              </CardContent>
              <CardFooter className="mt-auto justify-end gap-1">
                <PermissionButton
                  permission="plans:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Edit ${plan.name}`}
                  onClick={() => {
                    setEditing(plan)
                  }}
                >
                  <Pencil />
                </PermissionButton>
                <PermissionButton
                  permission="plans:write"
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${plan.name}`}
                  disabled={plan.is_default}
                  onClick={() => {
                    setDeleting(plan)
                  }}
                >
                  <Trash2 />
                </PermissionButton>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}
      {editing !== null && (
        <PlanDialog
          plan={editing === 'new' ? undefined : editing}
          onClose={() => {
            setEditing(null)
          }}
          onSave={(body) =>
            editing === 'new'
              ? mutations.create.mutateAsync(body)
              : mutations.update.mutateAsync({ id: editing.id, body })
          }
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        title={`Delete ${deleting?.name ?? ''}?`}
        description="A plan that consumers are still on can't be deleted: move them first."
        confirmLabel="Delete plan"
        destructive
        onConfirm={async () => {
          if (!deleting) return
          await mutations.remove.mutateAsync(deleting.id)
          toast.success(`Plan ${deleting.name} deleted`)
        }}
      />
    </>
  )
}

const schema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(100),
  rate_limit_per_min: z.coerce.number().int().min(1).max(100_000),
  monthly_quota: z.union([z.literal(''), z.coerce.number().int().min(1).max(1e12)]),
  is_default: z.boolean(),
  portal_visible: z.boolean(),
  requires_approval: z.boolean(),
})
type Values = z.input<typeof schema>

function PlanDialog({
  plan,
  onClose,
  onSave,
}: {
  plan?: Plan
  onClose: () => void
  onSave: (body: PlanCreate) => Promise<Plan>
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: plan?.name ?? '',
      rate_limit_per_min: plan?.rate_limit_per_min ?? 60,
      monthly_quota: plan?.monthly_quota ?? '',
      is_default: plan?.is_default ?? false,
      portal_visible: plan?.portal_visible ?? false,
      requires_approval: plan?.requires_approval ?? false,
    },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (raw) => {
    const values = schema.parse(raw)
    setError(null)
    try {
      const saved = await onSave({
        ...values,
        // A default can only be moved to another plan, never unset.
        is_default: values.is_default || undefined,
        // Empty: no monthly quota (null removes one from an edited plan).
        monthly_quota: values.monthly_quota === '' ? null : values.monthly_quota,
      })
      toast.success(`Plan ${saved.name} ${plan ? 'saved' : 'created'}`)
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
      title={plan ? `Edit ${plan.name}` : 'New plan'}
      submitLabel={plan ? 'Save' : 'Create plan'}
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="Name" placeholder="Gold" />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            control={form.control}
            name="rate_limit_per_min"
            label="Requests per minute"
            type="number"
          />
          <TextField
            control={form.control}
            name="monthly_quota"
            label="Requests per month"
            type="number"
            placeholder="No quota"
          />
        </div>
        {!plan?.is_default && (
          <SwitchField
            control={form.control}
            name="is_default"
            label="Default plan"
            description="New consumers get it unless another is chosen. Replaces the current default."
          />
        )}
        <SwitchField
          control={form.control}
          name="portal_visible"
          label="Offered on the developer portal"
          description="Developers can pick it for their apps."
        />
        <SwitchField
          control={form.control}
          name="requires_approval"
          label="Needs approval"
          description="Apps on this plan wait until an API Admin approves them."
        />
      </Form>
    </FormDialog>
  )
}
