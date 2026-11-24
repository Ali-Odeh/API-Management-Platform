import { zodResolver } from '@hookform/resolvers/zod'
import { ArrowLeft, Lock, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { z } from 'zod'
import { StatusBadge } from '@/components/badges'
import { TextField } from '@/components/fields'
import { FormDialog } from '@/components/form-dialog'
import { PageHeader } from '@/components/page-header'
import { ErrorState, LoadingState } from '@/components/states'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { formatNumber } from '@/lib/format'
import { showServerError } from '@/lib/forms'
import { summarizeDoc, type DocOperation } from './openapi-doc'
import { OpenApiDocs } from './openapi-docs'
import {
  useAppMutations,
  useCatalogApi,
  useOfferedPlans,
  type App,
  type CatalogApi,
  type OfferedPlan,
} from './queries'
import { TryIt, type TryRequest } from './try-it'

/** `/portal/apis/:slug/:id`: an API's docs, a try-it console, and creating an app for it. */
export function PortalApiPage() {
  const { slug = '', id = '' } = useParams()
  const api = useCatalogApi(slug, id)
  const [creating, setCreating] = useState(false)
  const [request, setRequest] = useState<TryRequest | null>(null)
  const doc = useMemo(() => (api.data?.openapi ? summarizeDoc(api.data.openapi) : null), [api.data])

  if (api.isPending) return <LoadingState />
  if (api.error) return <ErrorState error={api.error} onRetry={() => void api.refetch()} />
  const a = api.data
  const firstRoute = a.routes[0]
  const tryRequest = request ?? {
    method: firstRoute?.methods[0] ?? 'GET',
    path: firstRoute?.path_prefix ?? '/',
  }

  function tryOperation(op: DocOperation) {
    setRequest({ method: op.method, path: op.path })
    document.getElementById('try-it')?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <>
      <Button asChild variant="link" className="mb-2 px-0">
        <Link to="/portal">
          <ArrowLeft /> All APIs
        </Link>
      </Button>
      <PageHeader
        title={a.name}
        description={
          <span className="flex items-center gap-2">
            {doc?.version && <span>v{doc.version}</span>}
            <StatusBadge status={a.status} />
          </span>
        }
        actions={
          a.status === 'published' && (
            <Button
              onClick={() => {
                setCreating(true)
              }}
            >
              <Plus /> Create an app
            </Button>
          )
        }
      />
      <div className="grid gap-6">
        {a.status === 'deprecated' && (
          <Alert className="border-warning/50 bg-warning/10">
            <AlertDescription>
              This API is deprecated: existing apps keep working, but it takes no new apps.
            </AlertDescription>
          </Alert>
        )}
        {(a.description || doc?.description) && (
          <p className="text-sm whitespace-pre-wrap">{a.description || doc?.description}</p>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Routes</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-2 text-sm">
              {a.routes.map((r) => (
                <li key={r.path_prefix} className="flex flex-wrap items-center gap-2">
                  <code className="font-mono">{r.path_prefix}</code>
                  <span className="text-muted-foreground font-mono text-xs">
                    {r.methods.join(' ')}
                  </span>
                  {r.auth_required ? (
                    <span className="flex items-center gap-1 text-xs">
                      <Lock aria-hidden className="size-3" /> needs an API key
                    </span>
                  ) : (
                    <span className="text-muted-foreground text-xs">public</span>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <section>
          <h2 className="mb-3 text-lg font-semibold">Reference</h2>
          {doc ? (
            <OpenApiDocs doc={doc} onTry={tryOperation} />
          ) : (
            <p className="text-muted-foreground text-sm">This API has no documentation yet.</p>
          )}
        </section>
        <TryIt key={`${tryRequest.method} ${tryRequest.path}`} slug={slug} request={tryRequest} />
      </div>
      {creating && (
        <CreateAppDialog
          slug={slug}
          api={a}
          onClose={() => {
            setCreating(false)
          }}
        />
      )}
    </>
  )
}

const schema = z.object({
  name: z.string().trim().min(1, 'Name your app').max(100),
  plan_id: z.string().min(1, 'Choose a plan'),
})
type Values = z.infer<typeof schema>

function planLine(plan: OfferedPlan): string {
  const quota = plan.monthly_quota ? `, ${formatNumber(plan.monthly_quota)} a month` : ''
  const approval = plan.requires_approval ? ' · needs approval' : ''
  return `${plan.name}: ${formatNumber(plan.rate_limit_per_min)}/min${quota}${approval}`
}

/** An app for this API on one of the workspace's offered plans. */
function CreateAppDialog({
  slug,
  api,
  onClose,
}: {
  slug: string
  api: CatalogApi
  onClose: () => void
}) {
  const plans = useOfferedPlans(slug)
  const { create } = useAppMutations()
  const navigate = useNavigate()
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', plan_id: '' },
  })
  const [error, setError] = useState<string | null>(null)

  const submit = form.handleSubmit(async (values) => {
    setError(null)
    try {
      const app: App = await create.mutateAsync({ ...values, workspace: slug, api_id: api.id })
      toast.success(
        app.status === 'pending' ? `${app.name} is waiting for approval` : `${app.name} is ready`,
      )
      onClose()
      void navigate(`/portal/apps/${app.id}`)
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
      title={`An app for ${api.name}`}
      description="An app gets its own keys and the limits of its plan."
      submitLabel="Create app"
      pending={form.formState.isSubmitting}
      error={error}
      onSubmit={() => void submit()}
    >
      <Form {...form}>
        <TextField control={form.control} name="name" label="App name" placeholder="My shop" />
        <FormField
          control={form.control}
          name="plan_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Plan</FormLabel>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={plans.isPending ? 'Loading…' : 'Choose a plan'} />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {plans.data?.map((plan) => (
                    <SelectItem key={plan.id} value={plan.id}>
                      {planLine(plan)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormDescription>
                {plans.data?.length === 0
                  ? 'This workspace offers no plans to developers yet.'
                  : 'Plans that need approval wait until the API owner approves the app.'}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </Form>
    </FormDialog>
  )
}
