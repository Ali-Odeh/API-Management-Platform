import { zodResolver } from '@hookform/resolvers/zod'
import { ArrowLeft, Archive, Pencil, Rocket, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'
import { errorMessage } from '@/api/errors'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { TextAreaField, TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { FormError } from '@/components/form-error'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { ErrorState, LoadingState, Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Form } from '@/components/ui/form'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useAllRoutes } from '@/features/routes/queries'
import { useWorkspace } from '@/features/workspace/context'
import { showServerError } from '@/lib/forms'
import { describeOpenApi, parseOpenApi, readFileText } from './openapi'
import { useApi, useApiMutations, type ApiDetail, type ApiStatus } from './queries'

/** The moves the API allows from each status (PLAN.md, APIs). */
const TRANSITIONS: Record<ApiStatus, { to: ApiStatus; label: string; icon: typeof Rocket }[]> = {
  draft: [{ to: 'published', label: 'Publish', icon: Rocket }],
  published: [{ to: 'deprecated', label: 'Deprecate', icon: Archive }],
  deprecated: [{ to: 'published', label: 'Publish again', icon: Rocket }],
}

const STATUS_HELP: Record<ApiStatus, string> = {
  draft: 'Only members see it. Publish it to show it on the developer portal.',
  published: 'On the developer portal: developers can read its docs and create apps for it.',
  deprecated: 'Still on the portal and working for existing apps, but takes no new apps.',
}

/** `/w/:slug/apis/:id`: an API's routes, OpenAPI document and status. */
export function ApiDetailPage() {
  const { slug } = useWorkspace()
  const id = useParams().id ?? ''
  const navigate = useNavigate()
  const query = useApi(slug, id)
  const { update, remove } = useApiMutations(slug)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)

  if (query.isPending) return <LoadingState />
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const a = query.data

  async function move(to: ApiStatus) {
    try {
      await update.mutateAsync({ id: a.id, body: { status: to } })
      toast.success(`${a.name} is ${to}`)
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <>
      <Button asChild variant="link" className="mb-2 px-0">
        <Link to={`/w/${slug}/apis`}>
          <ArrowLeft /> APIs
        </Link>
      </Button>
      <PageHeader
        title={a.name}
        description={
          <span className="flex items-center gap-2">
            <span className="font-mono">{a.slug}</span>
            <StatusBadge status={a.status} />
          </span>
        }
        actions={
          <>
            {TRANSITIONS[a.status].map(({ to, label, icon: Icon }) => (
              <PermissionButton key={to} permission="apis:write" onClick={() => void move(to)}>
                <Icon /> {label}
              </PermissionButton>
            ))}
            <PermissionButton
              permission="apis:write"
              variant="outline"
              onClick={() => {
                setEditing(true)
              }}
            >
              <Pencil /> Edit
            </PermissionButton>
            <PermissionButton
              permission="apis:write"
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
      <p className="text-muted-foreground -mt-4 mb-6 text-sm">{STATUS_HELP[a.status]}</p>
      <div className="grid gap-6">
        {a.description && (
          <Card>
            <CardContent className="text-sm whitespace-pre-wrap">{a.description}</CardContent>
          </Card>
        )}
        <RoutesCard api={a} />
        <OpenApiCard api={a} />
      </div>
      {editing && (
        <EditApiDialog
          api={a}
          onClose={() => {
            setEditing(false)
          }}
          onSave={(body) => update.mutateAsync({ id: a.id, body })}
        />
      )}
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${a.name}?`}
        description="It leaves the portal. Its routes stay and keep working."
        confirmLabel="Delete API"
        destructive
        onConfirm={async () => {
          await remove.mutateAsync(a.id)
          toast.success(`${a.name} deleted`)
          void navigate(`/w/${slug}/apis`, { replace: true })
        }}
      />
    </>
  )
}

/** Which of the workspace's routes belong to this API. */
function RoutesCard({ api: a }: { api: ApiDetail }) {
  const { slug, can } = useWorkspace()
  const routes = useAllRoutes(slug)
  const { update } = useApiMutations(slug)
  const [selected, setSelected] = useState<Set<string>>(() => new Set(a.routes.map((r) => r.id)))
  const canWrite = can('apis:write') === 'allowed'
  const original = new Set(a.routes.map((r) => r.id))
  const changed = selected.size !== original.size || [...selected].some((id) => !original.has(id))

  async function save() {
    try {
      await update.mutateAsync({ id: a.id, body: { route_ids: [...selected] } })
      toast.success('Routes saved')
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Routes</CardTitle>
        <CardDescription>
          The paths this API is made of. A route belongs to one API at most.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {routes.isPending ? (
          <LoadingState />
        ) : routes.error ? (
          <ErrorState error={routes.error} />
        ) : routes.data.length === 0 ? (
          <p className="text-muted-foreground text-sm">The workspace has no routes yet.</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {routes.data.map((route) => (
              <li key={route.id}>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={selected.has(route.id)}
                    disabled={!canWrite}
                    onCheckedChange={(on) => {
                      const next = new Set(selected)
                      if (on) next.add(route.id)
                      else next.delete(route.id)
                      setSelected(next)
                    }}
                  />
                  <span className="font-mono">{route.path_prefix}</span>
                  <span className="text-muted-foreground text-xs">{route.methods.join(' ')}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {canWrite && (
          <div>
            <Button onClick={() => void save()} disabled={!changed || update.isPending}>
              {update.isPending && <Spinner />} Save routes
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function OpenApiCard({ api: a }: { api: ApiDetail }) {
  const { slug, can } = useWorkspace()
  const { update } = useApiMutations(slug)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [removing, setRemoving] = useState(false)
  const canWrite = can('apis:write') === 'allowed'
  const summary = a.openapi ? describeOpenApi(a.openapi) : null

  async function save() {
    const parsed = parseOpenApi(text)
    if ('error' in parsed) {
      setError(parsed.error)
      return
    }
    setError(null)
    try {
      await update.mutateAsync({ id: a.id, body: { openapi: parsed.doc } })
      setText('')
      toast.success('OpenAPI document saved')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>OpenAPI document</CardTitle>
        <CardDescription>
          The portal renders it as docs with a try-it console. JSON, up to 1 MiB.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {a.openapi ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm">
            <span>
              <strong>{summary?.title ?? 'Untitled'}</strong>
              {summary?.version && (
                <span className="text-muted-foreground"> v{summary.version}</span>
              )}
              <span className="text-muted-foreground"> · {summary?.paths} paths</span>
            </span>
            {canWrite && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setRemoving(true)
                }}
              >
                Remove
              </Button>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">No document yet.</p>
        )}
        {canWrite && (
          <div className="grid gap-2">
            <Label htmlFor="openapi-text">
              {a.openapi ? 'Replace it: paste' : 'Paste'} the JSON, or load a file
            </Label>
            <Textarea
              id="openapi-text"
              rows={6}
              className="font-mono text-xs"
              placeholder='{"openapi": "3.1.0", "info": {"title": "Orders", "version": "1.0"}, "paths": {}}'
              value={text}
              onChange={(e) => {
                setText(e.target.value)
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => void save()} disabled={!text.trim() || update.isPending}>
                {update.isPending && <Spinner />} Save document
              </Button>
              <Button asChild variant="outline">
                <label>
                  <Upload /> Load a .json file
                  <input
                    type="file"
                    accept="application/json,.json"
                    className="sr-only"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) void readFileText(file).then(setText)
                      e.target.value = ''
                    }}
                  />
                </label>
              </Button>
            </div>
            <FormError message={error} />
          </div>
        )}
      </CardContent>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title="Remove the document?"
        description="The API's portal page shows no docs until a new one is saved."
        confirmLabel="Remove document"
        destructive
        onConfirm={async () => {
          await update.mutateAsync({ id: a.id, body: { openapi: null } })
          toast.success('Document removed')
        }}
      />
    </Card>
  )
}

const editSchema = z.object({
  name: z.string().trim().min(1, 'Enter a name').max(100),
  description: z.string().max(2000),
})
type EditValues = z.infer<typeof editSchema>

function EditApiDialog({
  api: a,
  onClose,
  onSave,
}: {
  api: ApiDetail
  onClose: () => void
  onSave: (body: EditValues) => Promise<ApiDetail>
}) {
  const form = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: { name: a.name, description: a.description },
  })
  const [error, setError] = useState<string | null>(null)
  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      await onSave(values)
      toast.success('API saved')
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
      title={`Edit ${a.name}`}
      submitLabel="Save"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="Name" />
        <TextAreaField control={form.control} name="description" label="Description" rows={4} />
      </Form>
    </FormDialog>
  )
}
