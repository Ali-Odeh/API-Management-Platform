import { Building2, History, KeyRound, ShieldAlert, Code2, Users } from 'lucide-react'
import { Link, Outlet } from 'react-router'
import { Shell } from '@/components/layout/shell'
import { TopBar } from '@/components/layout/top-bar'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { useMe } from '@/features/me/queries'
import { useMemberAuth } from '@/lib/auth/member'

const LINKS = [
  { to: '/platform/workspaces', label: 'Workspaces', icon: Building2 },
  { to: '/platform/users', label: 'Users', icon: Users },
  { to: '/platform/developers', label: 'Developers', icon: Code2 },
  { to: '/platform/activity', label: 'Activity', icon: History },
]

/**
 * `/platform/*`: the Platform Admin's pages. Every platform endpoint needs
 * a session verified with MFA, so without one the pages explain how to get
 * it instead of failing call by call.
 */
export function PlatformLayout() {
  const auth = useMemberAuth()
  const me = useMe()

  let content: React.ReactNode = <Outlet />
  if (me.isPending) content = <LoadingState />
  else if (me.error) content = <ErrorState error={me.error} onRetry={() => void me.refetch()} />
  else if (!me.data.is_platform_admin) {
    content = (
      <EmptyState
        title="Only the Platform Admin can open these pages"
        action={
          <Button asChild variant="outline">
            <Link to="/">My workspaces</Link>
          </Button>
        }
      />
    )
  } else if (auth.aal !== 'aal2') content = <NeedsMfa />

  return (
    <Shell
      topBar={(menu) => <TopBar leading={menu} />}
      sidebar={me.data?.is_platform_admin ? { title: 'Platform', links: LINKS } : undefined}
    >
      {content}
    </Shell>
  )
}

/**
 * A Platform Admin without a verified authenticator. (One who has it but
 * skipped the code never gets here: sign-in asks for it first.)
 */
function NeedsMfa() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16 text-center">
      <ShieldAlert aria-hidden className="text-warning size-10" />
      <h1 className="text-xl font-semibold">The Platform pages need two-step verification</h1>
      <p className="text-muted-foreground text-sm">
        Set up an authenticator app; this session is then verified with its code.
      </p>
      <Button asChild>
        <Link to="/account">
          <KeyRound /> Set up an authenticator
        </Link>
      </Button>
    </div>
  )
}
