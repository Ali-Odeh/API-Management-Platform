import type { Role, Schema } from '@/api/types'
import { makeMyWorkspace, state } from './server'

const STAMP = '2026-11-20T10:00:00Z'

/** Makes the signed-in person a member of `acme` with this role. */
export function asRole(role: Role) {
  state.myWorkspaces = [makeMyWorkspace({ role })]
}

export function makeTarget(overrides: Partial<Schema<'TargetOut'>> = {}): Schema<'TargetOut'> {
  return {
    id: 't-1',
    url: 'http://orders:9000',
    weight: 100,
    is_active: true,
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeService(overrides: Partial<Schema<'ServiceOut'>> = {}): Schema<'ServiceOut'> {
  return {
    id: 's-1',
    name: 'orders',
    timeout_ms: 5000,
    is_active: true,
    lb_algorithm: 'round_robin',
    hc_path: '/health',
    hc_interval_s: 10,
    hc_unhealthy_threshold: 3,
    hc_healthy_threshold: 2,
    passive_failures: 5,
    passive_eject_s: 30,
    targets: [makeTarget()],
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeRoute(overrides: Partial<Schema<'RouteOut'>> = {}): Schema<'RouteOut'> {
  return {
    id: 'r-1',
    service_id: 's-1',
    path_prefix: '/v1/orders',
    methods: ['GET', 'POST'],
    strip_prefix: false,
    auth_required: true,
    is_active: true,
    max_body_bytes: null,
    cors: { origins: [], methods: [], headers: [], credentials: false, max_age: 600 },
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makePlan(overrides: Partial<Schema<'PlanOut'>> = {}): Schema<'PlanOut'> {
  return {
    id: 'p-1',
    name: 'Default',
    rate_limit_per_min: 60,
    monthly_quota: null,
    is_default: true,
    portal_visible: false,
    requires_approval: false,
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeConsumer(
  overrides: Partial<Schema<'ConsumerOut'>> = {},
): Schema<'ConsumerOut'> {
  return {
    id: 'c-1',
    name: 'mobile-app',
    email: 'mobile@example.test',
    plan_id: 'p-1',
    status: 'active',
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeKey(overrides: Partial<Schema<'KeyOut'>> = {}): Schema<'KeyOut'> {
  return {
    id: 'k-1',
    consumer_id: 'c-1',
    name: 'production',
    key_prefix: 'ak_live_3f9a',
    is_active: true,
    last_used_at: null,
    revoked_at: null,
    created_at: STAMP,
    ...overrides,
  }
}

export function makeMember(overrides: Partial<Schema<'MemberOut'>> = {}): Schema<'MemberOut'> {
  return {
    user_id: 'u-1',
    email: 'owner@example.test',
    role: 'owner',
    is_active: true,
    added_by: null,
    created_at: STAMP,
    ...overrides,
  }
}

export function makeEvent(
  overrides: Partial<Schema<'AuditEventOut'>> = {},
): Schema<'AuditEventOut'> {
  return {
    id: 'e-1',
    action: 'route.update',
    actor_type: 'user',
    actor_email: 'owner@example.test',
    actor_user_id: 'u-1',
    target_type: 'route',
    target_id: 'r-1',
    target_name: '/v1/orders',
    workspace_id: 'w-1',
    changes: { methods: { old: ['GET'], new: ['GET', 'POST'] } },
    ip: '10.0.0.1',
    user_agent: 'Mozilla/5.0',
    request_id: 'req-1',
    created_at: STAMP,
    ...overrides,
  }
}

const LATENCY = { p50_ms: 12, p95_ms: 48.4, p99_ms: 90 }

export function makeTraffic(requests = 120): Schema<'TrafficOut'> {
  return {
    start: '2026-11-22T10:00:00Z',
    end: '2026-11-23T10:00:00Z',
    interval: 'hour',
    totals: { requests, client_errors: 6, server_errors: 2, latency: LATENCY },
    buckets: [
      {
        start: '2026-11-22T10:00:00Z',
        requests,
        client_errors: 6,
        server_errors: 2,
        latency: LATENCY,
      },
    ],
  }
}

export function makeLog(overrides: Partial<Schema<'RequestLogOut'>> = {}): Schema<'RequestLogOut'> {
  return {
    event_id: 'ev-1',
    ts: '2026-11-23T09:59:00Z',
    method: 'GET',
    path: '/v1/orders/42',
    status: 200,
    latency_ms: 14.2,
    upstream_ms: 11,
    bytes: 512,
    route_id: 'r-1',
    consumer_id: 'c-1',
    key_id: 'k-1',
    target_id: 't-1',
    client_ip: '10.0.0.9',
    ratelimit: 'ok',
    request_id: 'req-9',
    ...overrides,
  }
}

export function makeApi(overrides: Partial<Schema<'ApiOut'>> = {}): Schema<'ApiOut'> {
  return {
    id: 'a-1',
    name: 'Orders API',
    slug: 'orders',
    description: 'Place and track orders.',
    status: 'draft',
    has_openapi: false,
    openapi: null,
    routes: [{ id: 'r-1', path_prefix: '/v1/orders', methods: ['GET'], auth_required: true }],
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}

export function makeApp(overrides: Partial<Schema<'AppAdminOut'>> = {}): Schema<'AppAdminOut'> {
  return {
    id: 'app-1',
    name: 'Shop frontend',
    developer_email: 'dev@example.test',
    consumer_id: 'c-9',
    status: 'pending',
    api: { id: 'a-1', name: 'Orders API', slug: 'orders', status: 'published' },
    plan: { id: 'p-2', name: 'Gold', rate_limit_per_min: 600, monthly_quota: 100000 },
    created_at: STAMP,
    updated_at: STAMP,
    ...overrides,
  }
}
