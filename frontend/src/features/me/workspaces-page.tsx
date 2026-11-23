import { ArrowRight, LogOut, Shield } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'
import type { MyWorkspace } from '@/api/types'
import { RoleBadge, StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useLeaveWorkspace, useMe, useMyWorkspaces } from './queries'

/** `/`: my workspaces and my role in each. */
export function WorkspacesPage() {
  const { data: me } = useMe()
  const workspaces = useMyWorkspaces()
  const [leaving, setLeaving] = useState<MyWorkspace | null>(null)
  const leave = useLeaveWorkspace()

  return (
    <>
      <PageHeader
        title="My workspaces"
        description="Open one to manage its services, routes and consumers."
        actions={
          me?.is_platform_admin && (
            <Button asChild variant="outline">
              <Link to="/platform">
                <Shield /> Platform
              </Link>
            </Button>
          )
        }
      />
      {workspaces.isPending ? (
        <LoadingState />
      ) : workspaces.error ? (
        <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} />
      ) : workspaces.data.length === 0 ? (
        <EmptyState
          title="No workspaces yet"
          description={
            me?.is_platform_admin
              ? 'Create one on the Platform pages.'
              : 'An owner of a workspace can add you by your email.'
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {workspaces.data.map((ws) => (
            <Card key={ws.slug}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2">
                  <span className="truncate">{ws.name}</span>
                  <RoleBadge role={ws.role} />
                </CardTitle>
                <CardDescription className="flex items-center gap-2">
                  <span className="truncate font-mono">{ws.slug}</span>
                  {ws.status !== 'active' && <StatusBadge status={ws.status} />}
                </CardDescription>
                {ws.status === 'suspended' && ws.status_reason && (
                  <p className="text-muted-foreground text-sm">{ws.status_reason}</p>
                )}
              </CardHeader>
              <CardFooter className="mt-auto justify-between gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setLeaving(ws)
                  }}
                >
                  <LogOut /> Leave
                </Button>
                <Button asChild size="sm">
                  <Link to={`/w/${ws.slug}`}>
                    Open <ArrowRight />
                  </Link>
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}
      <ConfirmDialog
        open={leaving !== null}
        onOpenChange={(open) => {
          if (!open) setLeaving(null)
        }}
        title={`Leave ${leaving?.name ?? ''}?`}
        description="You lose access to this workspace until an owner adds you again."
        confirmLabel="Leave workspace"
        destructive
        onConfirm={async () => {
          if (!leaving) return
          await leave.mutateAsync(leaving.slug)
          toast.success(`You left ${leaving.name}`)
        }}
      />
    </>
  )
}
