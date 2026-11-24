import { ArrowLeft, Clock, Plus, Trash2, XCircle } from 'lucide-react'
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
import { ShownOnceDialog } from '@/components/shown-once-dialog'
import { formatDate, formatNumber, formatRelative } from '@/lib/format'
import type { RangeKey } from '@/lib/time-range'
import { gatewayUrl } from './gateway'
import { useAppKeys, useAppMutations, useAppUsage, useMyApp, type App } from './queries'

type Key = NonNullable<ReturnType<typeof useAppKeys>['data']>[number]

/** `/portal/apps/:id`: an app's status, keys, usage and quota. */
export function PortalAppPage() {
  const id = useParams().id ?? ''
  const navigate = useNavigate()
  const app = useMyApp(id)
  const { remove } = useAppMutations()
  const [deleting, setDeleting] = useState(false)

  if (app.isPending) return <LoadingState />
  if (app.error) return <ErrorState error={app.error} onRetry={() => void app.refetch()} />
  const a = app.data

  return (
    <>
      <Button asChild variant="link" className="mb-2 px-0">
        <Link to="/portal/apps">
          <ArrowLeft /> My apps
        </Link>
      </Button>
      <PageHeader
        title={a.name}
        description={<StatusBadge status={a.status} />}
        actions={
          <Button
            variant="outline"
            onClick={() => {
              setDeleting(true)
            }}
          >
            <Trash2 /> Delete app
          </Button>
        }
      />
      <div className="grid gap-6">
        {a.status === 'pending' && (
          <Alert className="border-warning/50 bg-warning/10">
            <Clock />
            <AlertTitle>Waiting for approval</AlertTitle>
            <AlertDescription>
              The {a.plan.name} plan needs the API owner's approval. You can create keys once the
              app is approved.
            </AlertDescription>
          </Alert>
        )}
        {a.status === 'rejected' && (
          <Alert variant="destructive">
            <XCircle />
            <AlertTitle>Rejected</AlertTitle>
            <AlertDescription>
              The API owner rejected this app: its keys don't work. Contact them, or create another
              app.
            </AlertDescription>
          </Alert>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent>
            <Details
              items={[
                [
                  'API',
                  <Link
                    key="api"
                    to={`/portal/apis/${a.workspace.slug}/${a.api.id}`}
                    className="hover:underline"
                  >
                    {a.api.name}
                  </Link>,
                ],
                ['Offered by', a.workspace.name],
                [
                  'Plan',
                  `${a.plan.name}: ${formatNumber(a.plan.rate_limit_per_min)} requests a minute${
                    a.plan.monthly_quota ? `, ${formatNumber(a.plan.monthly_quota)} a month` : ''
                  }`,
                ],
                [
                  'Gateway',
                  <code key="url" className="text-xs">
                    {gatewayUrl(a.workspace.slug, '/')}
                  </code>,
                ],
                ['Created', formatDate(a.created_at)],
              ]}
            />
          </CardContent>
        </Card>
        <KeysSection app={a} />
        <UsageSection app={a} />
      </div>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${a.name}?`}
        description="Its keys stop working at once. This can't be undone."
        confirmLabel="Delete app"
        destructive
        onConfirm={async () => {
          await remove.mutateAsync(a.id)
          toast.success(`${a.name} deleted`)
          void navigate('/portal/apps', { replace: true })
        }}
      />
    </>
  )
}

function KeysSection({ app }: { app: App }) {
  const approved = app.status === 'approved'
  const keys = useAppKeys(app.id, approved)
  const { createKey, revokeKey } = useAppMutations()
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<{ key: string; name: string } | null>(null)
  const [revoking, setRevoking] = useState<Key | null>(null)

  async function generate() {
    setError(null)
    try {
      const key = await createKey.mutateAsync({ id: app.id, name: name.trim() })
      setNaming(false)
      setName('')
      setCreated(key)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <section>
      <PageHeader
        level={2}
        title="API keys"
        description="Send a key in the X-API-Key header. At most 10 active keys."
        actions={
          approved && (
            <Button
              variant="outline"
              onClick={() => {
                setNaming(true)
              }}
            >
              <Plus /> Generate key
            </Button>
          )
        }
      />
      {approved ? (
        <DataTable<Key>
          rows={keys.data}
          isPending={keys.isPending}
          error={keys.error}
          onRetry={() => void keys.refetch()}
          rowKey={(k) => k.id}
          empty={{ title: 'No keys yet', description: 'Generate one to call the API.' }}
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
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setRevoking(k)
                    }}
                  >
                    Revoke
                  </Button>
                ),
            },
          ]}
        />
      ) : (
        <p className="text-muted-foreground text-sm">
          Keys are available once the app is approved.
        </p>
      )}
      <Dialog open={naming} onOpenChange={setNaming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Generate a key</DialogTitle>
            <DialogDescription>Name it after where you'll use it.</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              void generate()
            }}
          >
            <Label htmlFor="portal-key-name">Name</Label>
            <Input
              id="portal-key-name"
              value={name}
              maxLength={100}
              placeholder="laptop"
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
              <Button type="submit" disabled={createKey.isPending || !name.trim()}>
                {createKey.isPending && <Spinner />} Generate
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
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
          await revokeKey.mutateAsync({ id: app.id, keyId: revoking.id })
          toast.success(`Key ${revoking.name} revoked`)
        }}
      />
    </section>
  )
}

function UsageSection({ app }: { app: App }) {
  const [range, setRange] = useState<RangeKey>('24h')
  const usage = useAppUsage(app.id, range)
  const quota = usage.data?.quota
  const share =
    quota?.monthly_quota != null && quota.monthly_quota > 0
      ? Math.min(100, (quota.used / quota.monthly_quota) * 100)
      : null

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Usage</CardTitle>
        <RangeSelect value={range} onChange={setRange} />
      </CardHeader>
      <CardContent className="grid gap-6">
        {usage.isPending ? (
          <LoadingState />
        ) : usage.error ? (
          <ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
        ) : (
          <>
            <div className="grid gap-2">
              <div className="flex flex-wrap justify-between gap-2 text-sm">
                <span>This month</span>
                <span className="text-muted-foreground">
                  {usage.data.quota.monthly_quota != null
                    ? `${formatNumber(usage.data.quota.used)} of ${formatNumber(usage.data.quota.monthly_quota)} requests · resets ${formatDate(usage.data.quota.resets_at)}`
                    : `${formatNumber(usage.data.quota.used)} requests · no monthly quota`}
                </span>
              </div>
              {share !== null && (
                <div
                  className="bg-muted h-2 overflow-hidden rounded-full"
                  role="progressbar"
                  aria-label="Monthly quota used"
                  aria-valuenow={Math.round(share)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className={share >= 90 ? 'bg-destructive h-full' : 'bg-chart-1 h-full'}
                    style={{ width: `${share}%` }}
                  />
                </div>
              )}
            </div>
            <TrafficChart traffic={usage.data.traffic} />
          </>
        )}
      </CardContent>
    </Card>
  )
}
