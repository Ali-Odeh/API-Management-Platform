import type { GoTrueClient } from '@supabase/auth-js'
import createClient, { type Middleware } from 'openapi-fetch'
import { developerAuth, memberAuth } from '@/lib/auth/gotrue'
import { ApiError } from './errors'
import type { paths } from './schema'

/**
 * Codes after which the session can't be used any more: the person is
 * signed out of this browser, and the guards send them to the login page.
 */
const SESSION_ENDING_403 = new Set(['user_disabled', 'account_conflict'])

/**
 * Adds the GoTrue access token to every call. `getSession` refreshes an
 * expired token first, so a 401 means the session really is over.
 */
function authMiddleware(auth: GoTrueClient): Middleware {
  return {
    async onRequest({ request }) {
      const { data } = await auth.getSession()
      const token = data.session?.access_token
      if (token) request.headers.set('Authorization', `Bearer ${token}`)
      return request
    },
    async onResponse({ response }) {
      if (response.status === 401) await auth.signOut({ scope: 'local' })
      if (response.status === 403) {
        const body: unknown = await response
          .clone()
          .json()
          .catch(() => null)
        const detail = (body as { detail?: unknown } | null)?.detail
        if (typeof detail === 'string' && SESSION_ENDING_403.has(detail)) {
          await auth.signOut({ scope: 'local' })
        }
      }
      return response
    },
  }
}

function createApi(auth: GoTrueClient) {
  const client = createClient<paths>({
    baseUrl: window.location.origin,
    // Looked up on each call, so a fetch replaced later (tests' mock server) is used.
    fetch: (request) => globalThis.fetch(request),
  })
  client.use(authMiddleware(auth))
  return client
}

/** The control plane's admin API, as a signed-in member. */
export const api = createApi(memberAuth)
/** The portal API (`/api/v1/portal/...`), as a signed-in developer. */
export const portalApi = createApi(developerAuth)

interface FetchResult<T> {
  data?: T
  error?: unknown
  response: Response
}

/**
 * The data of a successful call, or an ApiError. Every query and mutation
 * goes through it, so components only ever see typed data or ApiError.
 */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>
  try {
    result = await call
  } catch {
    throw new ApiError(0, 'The server could not be reached')
  }
  if (!result.response.ok) throw ApiError.fromBody(result.response.status, result.error)
  return result.data as T
}

/** Lists are paged at most 100 at a time; this reads every page. */
export async function fetchAll<T>(
  page: (page: number) => Promise<FetchResult<{ items: T[]; total: number }>>,
): Promise<T[]> {
  const items: T[] = []
  for (let n = 1; ; n++) {
    const result = await unwrap(page(n))
    items.push(...result.items)
    if (items.length >= result.total || result.items.length === 0) return items
  }
}
