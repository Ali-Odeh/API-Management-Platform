import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { Providers } from '@/app/providers'
import { createQueryClient } from '@/app/query-client'
import { routes } from '@/routes/routes'
import { DEVELOPER_STORAGE_KEY, developerGoTrue, MEMBER_STORAGE_KEY, memberGoTrue } from './server'

/** Renders the whole app at `path`, with the real routes and providers. */
export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const queryClient = createQueryClient()
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } })
  const user = userEvent.setup()
  const view = render(
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  )
  return { ...view, router, user, queryClient }
}

/** A member signed in earlier (stored session), then the app at `path`. */
export function renderSignedIn(
  path: string,
  options: { email?: string; aal?: 'aal1' | 'aal2'; mfa?: boolean } = {},
) {
  const email = options.email ?? 'owner@example.test'
  memberGoTrue.addUser(email, 'correct-horse-battery', { mfa: options.mfa ?? false })
  memberGoTrue.storeSession(MEMBER_STORAGE_KEY, email, options.aal ?? 'aal1')
  return renderApp(path)
}

/** A developer signed in on the portal earlier, then the app at `path`. */
export function renderAsDeveloper(path: string, email = 'dev@example.com') {
  developerGoTrue.addUser(email, 'correct-horse-battery')
  developerGoTrue.storeSession(DEVELOPER_STORAGE_KEY, email)
  return renderApp(path)
}
