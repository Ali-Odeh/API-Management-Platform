import { AlertTriangle } from 'lucide-react'
import { useMemo } from 'react'
import { Link, Outlet, useParams } from 'react-router'
import { ApiError } from '@/api/errors'
import { Shell } from '@/components/layout/shell'
import { TopBar } from '@/components/layout/top-bar'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useMe, useMyWorkspaces } from '@/features/me/queries'
import { useMemberAuth } from '@/lib/auth/member'
import type { Access } from '@/lib/permissions'
import { decide } from '@/lib/permissions'
import { makeWorkspaceContext, useWorkspace, WorkspaceContext } from './context'
import { WORKSPACE_NAV } from './nav'
import { useWorkspaceDetail } from './queries'

/** `/w/:slug/*`: the dashboard frame of one workspace. */
export function WorkspaceLayout() {
  const slug = useParams().slug ?? ''
  const auth = useMemberAuth()
  const me = useMe()
  const mine = useMyWorkspaces()
  const detail = useWorkspaceDetail(slug)

  const membership = mine.data?.find((ws) => ws.slug === slug)
  const workspace = detail.data

  const context = useMemo(() => {
    if (!workspace || !me.data) return null
    const access: Access = {
      role: membership?.role ?? null,
      isPlatformAdmin: me.data.is_platform_admin,
      aal: auth.aal,
      workspaceStatus: workspace.status,
    }
    return makeWorkspaceContext(workspace, access)
  }, [workspace, me.data, membership?.role, auth.aal])

  const links = context
    ? WORKSPACE_NAV.filter((item) => decide(context.access, item.permission) !== 'denied').map(
        (item) => ({
          to: item.path ? `/w/${slug}/${item.path}` : `/w/${slug}`,
          label: item.label,
          icon: item.icon,
          end: item.path === '',
        }),
      )
    : []

  const current = {
    slug,
    name: workspace?.name ?? membership?.name ?? slug,
    role: membership?.role ?? null,
  }

  return (
    <Shell
      topBar={(menu) => <TopBar current={current} leading={menu} />}
      sidebar={context ? { title: workspace?.name, links } : undefined}
    >
      {detail.isPending || me.isPending || mine.isPending ? (
        <LoadingState />
      ) : detail.error ? (
        <WorkspaceError error={detail.error} retry={() => void detail.refetch()} />
      ) : context ? (
        <WorkspaceContext.Provider value={context}>
          <StatusBanner />
          <Outlet />
        </WorkspaceContext.Provider>
      ) : (
        <ErrorState error={me.error ?? mine.error} />
      )}
    </Shell>
  )
}

function WorkspaceError({ error, retry }: { error: Error; retry: () => void }) {
  if (error instanceof ApiError && error.status === 404) {
    return (
      <EmptyState
        title="Workspace not found"
        description="It doesn't exist, was deleted, or you are not a member of it."
        action={
          <Button asChild variant="outline">
            <Link to="/">My workspaces</Link>
          </Button>
        }
      />
    )
  }
  if (error instanceof ApiError && error.detail === 'mfa_required') {
    return (
      <EmptyState
        title="Verify with MFA to open this workspace"
        description="You are not a member here: the Platform Admin's access needs a sign-in verified with your authenticator app. Sign out and sign in again with your code."
      />
    )
  }
  return <ErrorState error={error} onRetry={retry} />
}

/** A suspended workspace is read-only; members see why. */
function StatusBanner() {
  const { workspace } = useWorkspace()
  if (workspace.status !== 'suspended') return null
  return (
    <Alert className="border-warning/50 bg-warning/10 mb-6">
      <AlertTriangle />
      <AlertTitle>This workspace is suspended</AlertTitle>
      <AlertDescription>
        {workspace.status_reason ?? 'The Platform Admin suspended it.'} Its APIs answer 403 and
        nothing can be changed until it is unsuspended.
      </AlertDescription>
    </Alert>
  )
}
