import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { api, unwrap } from '@/api/client'
import { errorMessage } from '@/api/errors'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { PageHeader } from '@/components/page-header'
import { PermissionButton } from '@/components/permission-button'
import { Spinner } from '@/components/states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { meKeys } from '@/features/me/queries'
import { formatNumber } from '@/lib/format'
import { LIMIT_LABELS } from '@/lib/limits'
import { useWorkspace } from './context'
import { workspaceKeys } from './queries'

/** `/w/:slug/settings`: name, limits (set by the Platform Admin), delete. */
export function SettingsPage() {
  const { slug, workspace, can } = useWorkspace()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState(workspace.name)
  const [deleting, setDeleting] = useState(false)
  const canRename = can('workspace:write') === 'allowed'

  const rename = useMutation({
    mutationFn: (next: string) =>
      unwrap(
        api.PATCH('/api/v1/workspaces/{slug}', {
          params: { path: { slug } },
          body: { name: next },
        }),
      ),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(slug) }),
        queryClient.invalidateQueries({ queryKey: meKeys.workspaces }),
      ]),
  })

  async function saveName() {
    try {
      await rename.mutateAsync(name.trim())
      toast.success('Workspace renamed')
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle>General</CardTitle>
            <CardDescription>
              The slug <code className="font-mono">{slug}</code> never changes: it is the gateway
              host (<code className="font-mono">{slug}.localhost</code>) and part of every URL.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="flex max-w-md items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                void saveName()
              }}
            >
              <div className="grid flex-1 gap-2">
                <Label htmlFor="workspace-name">Name</Label>
                <Input
                  id="workspace-name"
                  value={name}
                  maxLength={100}
                  disabled={!canRename}
                  onChange={(e) => {
                    setName(e.target.value)
                  }}
                />
              </div>
              {canRename && (
                <Button
                  type="submit"
                  disabled={rename.isPending || !name.trim() || name.trim() === workspace.name}
                >
                  {rename.isPending && <Spinner />} Save
                </Button>
              )}
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Limits</CardTitle>
            <CardDescription>
              Set by the Platform Admin. "Custom" marks a limit set for this workspace; the others
              are the platform's defaults.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              {LIMIT_LABELS.map(([key, label]) => (
                <div
                  key={key}
                  className="flex items-center justify-between gap-2 rounded-md border p-3"
                >
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="flex items-center gap-2 font-medium">
                    {formatNumber(workspace.limits[key])}
                    {workspace.limit_overrides[key] != null && (
                      <Badge variant="outline">Custom</Badge>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>

        {can('workspace:delete') !== 'denied' && (
          <Card className="border-destructive/40">
            <CardHeader>
              <CardTitle>Delete this workspace</CardTitle>
              <CardDescription>
                Its APIs stop at once. The Platform Admin can restore it for 30 days; after that all
                its data is removed for good.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <PermissionButton
                permission="workspace:delete"
                variant="destructive"
                onClick={() => {
                  setDeleting(true)
                }}
              >
                <Trash2 /> Delete workspace
              </PermissionButton>
            </CardContent>
          </Card>
        )}
      </div>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${workspace.name}?`}
        description="Everyone loses access and its APIs stop answering."
        confirmLabel="Delete workspace"
        destructive
        typeToConfirm={slug}
        onConfirm={async () => {
          await unwrap(
            api.DELETE('/api/v1/workspaces/{slug}', {
              params: { path: { slug } },
              body: { confirm: slug },
            }),
          )
          toast.success(`${workspace.name} deleted`)
          await queryClient.invalidateQueries({ queryKey: meKeys.workspaces })
          void navigate('/', { replace: true })
        }}
      />
    </>
  )
}
