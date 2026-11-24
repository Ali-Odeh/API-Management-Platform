import { useQueryClient } from '@tanstack/react-query'
import { AppWindow, Ban, LibraryBig, LogOut, User } from 'lucide-react'
import { useEffect } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation } from 'react-router'
import { ApiError } from '@/api/errors'
import { ThemeMenu } from '@/components/layout/top-bar'
import { Logo } from '@/components/logo'
import { EmptyState, ErrorState, FullPageLoading, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { DeveloperAuthProvider, useDeveloperAuth } from '@/lib/auth/developer'
import { cn } from '@/lib/utils'
import { usePortalMe } from './queries'

/** Everything under `/portal` belongs to the developers' GoTrue. */
export function PortalRoot() {
  return (
    <DeveloperAuthProvider>
      <Outlet />
    </DeveloperAuthProvider>
  )
}

/** Portal pages need a signed-in developer; others go to the portal's sign-in. */
export function RequireDeveloper() {
  const auth = useDeveloperAuth()
  const location = useLocation()
  const queryClient = useQueryClient()
  const signedOut = auth.status === 'signed_out'

  useEffect(() => {
    if (signedOut) queryClient.removeQueries({ queryKey: ['portal'] })
  }, [signedOut, queryClient])

  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status !== 'signed_in') {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/portal/login?next=${next}`} replace />
  }
  if (auth.passwordSetupPending) return <Navigate to="/portal/set-password" replace />
  return <Outlet />
}

const LINKS = [
  { to: '/portal', label: 'APIs', icon: LibraryBig, end: true },
  { to: '/portal/apps', label: 'My apps', icon: AppWindow, end: false },
]

/** The portal's frame: its own top bar, no workspace sidebar. */
export function PortalLayout() {
  const auth = useDeveloperAuth()
  const me = usePortalMe()
  const blocked = me.error instanceof ApiError && me.error.detail === 'developer_blocked'

  return (
    <div className="flex min-h-svh flex-col">
      <header className="bg-background/95 sticky top-0 z-30 flex h-14 items-center gap-4 border-b px-4">
        <Link to="/portal" aria-label="Developer portal home">
          <Logo label="Developer portal" />
        </Link>
        <nav aria-label="Portal" className="flex items-center gap-1">
          {LINKS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm',
                  isActive ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-accent',
                )
              }
            >
              <Icon aria-hidden className="size-4" />
              <span className="hidden sm:inline">{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <ThemeMenu />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Account menu" className="gap-2">
                <User />
                <span className="hidden max-w-48 truncate md:inline">
                  {auth.session?.user.email}
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel className="font-normal">
                {auth.session?.user.email}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => void auth.signOut()}>
                <LogOut /> Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <main className="flex-1 p-4 md:p-8">
        <div className="mx-auto max-w-5xl">
          {me.isPending ? (
            <LoadingState />
          ) : blocked ? (
            <EmptyState
              title="Your developer account is blocked"
              description="The Platform Admin blocked it: the portal is closed to you and your apps' keys don't work. Contact the platform's operators."
              action={<Ban aria-hidden className="text-destructive size-6" />}
            />
          ) : me.error ? (
            <ErrorState error={me.error} onRetry={() => void me.refetch()} />
          ) : (
            <Outlet />
          )}
        </div>
      </main>
    </div>
  )
}
