import { developerAuth } from './gotrue'
import { createAuth } from './session'

/** Sign-in state of developers on the portal (their own GoTrue). */
export const { AuthProvider: DeveloperAuthProvider, useAuth: useDeveloperAuth } = createAuth(
  developerAuth,
  'Developer',
)
