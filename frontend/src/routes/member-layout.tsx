import { Outlet } from 'react-router'
import { Shell } from '@/components/layout/shell'
import { TopBar } from '@/components/layout/top-bar'
import { MemberAuthProvider } from '@/lib/auth/member'

/** Everything outside the portal belongs to members' GoTrue. */
export function MemberRoot() {
  return (
    <MemberAuthProvider>
      <Outlet />
    </MemberAuthProvider>
  )
}

/** Member pages without a workspace sidebar: my workspaces, account. */
export function MemberLayout() {
  return (
    <Shell topBar={(menu) => <TopBar leading={menu} />}>
      <Outlet />
    </Shell>
  )
}
