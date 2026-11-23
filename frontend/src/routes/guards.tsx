import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router'
import { FullPageLoading } from '@/components/states'
import { useMemberAuth } from '@/lib/auth/member'

/**
 * Member pages need a complete sign-in (with the MFA step when the person
 * has an authenticator). Anyone else goes to the login page, which brings
 * them back here afterwards.
 */
export function RequireMember() {
  const auth = useMemberAuth()
  const location = useLocation()
  const queryClient = useQueryClient()
  const signedOut = auth.status === 'signed_out'

  // Nothing read for one person may be shown to the next.
  useEffect(() => {
    if (signedOut) queryClient.clear()
  }, [signedOut, queryClient])

  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status !== 'signed_in') {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }
  if (auth.passwordSetupPending) return <Navigate to="/set-password" replace />
  return <Outlet />
}
