import { memberAuth } from './gotrue'
import { createAuth } from './session'

/** Sign-in state of workspace members and the Platform Admin. */
export const { AuthProvider: MemberAuthProvider, useAuth: useMemberAuth } = createAuth(
  memberAuth,
  'Member',
)
