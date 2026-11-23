import { http, HttpResponse, type JsonBodyType } from 'msw'
import { setupServer } from 'msw/node'
import type { MyWorkspace, User, WorkspaceDetail } from '@/api/types'
import { FakeGoTrue } from './gotrue'

export const ORIGIN = window.location.origin
export const MEMBER_STORAGE_KEY = 'apim-member-auth'
export const DEVELOPER_STORAGE_KEY = 'apim-developer-auth'

export const memberGoTrue = new FakeGoTrue(`${ORIGIN}/auth/v1`)
export const developerGoTrue = new FakeGoTrue(`${ORIGIN}/portal-auth/v1`)

/** A full URL of the control plane's API, for `http.get(api('/api/v1/me'), ...)`. */
export function api(path: string): string {
  return `${ORIGIN}${path}`
}

export function page<T>(items: T[]): { items: T[]; total: number } {
  return { items, total: items.length }
}

export function json(body: JsonBodyType, status = 200) {
  return HttpResponse.json(body, { status })
}

export function detail(status: number, text: string) {
  return HttpResponse.json({ detail: text }, { status })
}

const STAMP = '2026-11-20T10:00:00Z'

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u-1',
    email: 'owner@example.test',
    is_active: true,
    is_platform_admin: false,
    created_at: STAMP,
    ...overrides,
  }
}

const LIMITS = {
  rate_limit_per_min: 6000,
  max_concurrent: 200,
  max_services: 100,
  max_routes: 500,
  max_consumers: 5000,
  ip_rate_limit_per_min: 600,
}

const NO_OVERRIDES = {
  rate_limit_per_min: null,
  max_concurrent: null,
  max_services: null,
  max_routes: null,
  max_consumers: null,
  ip_rate_limit_per_min: null,
}

export function makeMyWorkspace(overrides: Partial<MyWorkspace> = {}): MyWorkspace {
  return {
    id: 'w-1',
    slug: 'acme',
    name: 'Acme',
    role: 'owner',
    status: 'active',
    status_reason: null,
    deleted_at: null,
    purge_after: null,
    created_at: STAMP,
    limits: LIMITS,
    limit_overrides: NO_OVERRIDES,
    ...overrides,
  }
}

export function makeWorkspaceDetail(overrides: Partial<WorkspaceDetail> = {}): WorkspaceDetail {
  return {
    id: 'w-1',
    slug: 'acme',
    name: 'Acme',
    status: 'active',
    status_reason: null,
    deleted_at: null,
    purge_after: null,
    created_at: STAMP,
    limits: LIMITS,
    limit_overrides: NO_OVERRIDES,
    usage: { services: 2, routes: 3, consumers: 4 },
    ...overrides,
  }
}

/**
 * What the default handlers answer; tests change it before rendering, or
 * add their own handlers with `server.use(...)`.
 */
export const state = {
  me: makeUser(),
  myWorkspaces: [makeMyWorkspace()] as MyWorkspace[],
  workspaces: new Map<string, WorkspaceDetail>([['acme', makeWorkspaceDetail()]]),
}

export function resetState() {
  state.me = makeUser()
  state.myWorkspaces = [makeMyWorkspace()]
  state.workspaces = new Map([['acme', makeWorkspaceDetail()]])
}

const NO_LATENCY = { p50_ms: null, p95_ms: null, p99_ms: null }
const NO_COUNTS = { requests: 0, client_errors: 0, server_errors: 0, latency: NO_LATENCY }

const defaultHandlers = [
  // A workspace without traffic, unless a test says otherwise.
  http.get(api('/api/v1/workspaces/:slug/analytics/traffic'), () =>
    json({ start: STAMP, end: STAMP, interval: 'hour', totals: NO_COUNTS, buckets: [] }),
  ),
  http.get(api('/api/v1/workspaces/:slug/analytics/routes'), () =>
    json({ start: STAMP, end: STAMP, items: [] }),
  ),
  http.get(api('/api/v1/workspaces/:slug/analytics/consumers'), () =>
    json({ start: STAMP, end: STAMP, items: [] }),
  ),
  http.get(api('/api/v1/workspaces/:slug/request-logs'), () => json(page([]))),
  http.get(api('/api/v1/me'), () => json(state.me)),
  http.get(api('/api/v1/me/workspaces'), () => json(page(state.myWorkspaces))),
  http.get(api('/api/v1/workspaces/:slug'), ({ params }) => {
    const ws = state.workspaces.get(String(params.slug))
    return ws ? json(ws) : detail(404, 'Workspace not found')
  }),
]

export const server = setupServer(
  ...memberGoTrue.handlers(),
  ...developerGoTrue.handlers(),
  ...defaultHandlers,
)
