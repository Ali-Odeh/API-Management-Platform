import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { Schema } from '@/api/types'
import { makeKey, makeTraffic } from '@/test/fixtures'
import { renderApp, renderAsDeveloper } from '@/test/render'
import { api, detail, developerGoTrue, json, page, server } from '@/test/server'

const STAMP = '2026-11-20T10:00:00Z'
const P = '/api/v1/portal'

const DOC = {
  openapi: '3.1.0',
  info: { title: 'Orders', version: '2.1', description: 'Orders of the shop.' },
  paths: {
    '/v1/orders/{id}': {
      get: {
        summary: 'One order',
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'string' } }],
        responses: { '200': { description: 'The order' }, '404': { description: 'No such order' } },
      },
    },
  },
}

function catalogApi(overrides: Partial<Schema<'CatalogApi'>> = {}): Schema<'CatalogApi'> {
  return {
    id: 'a-1',
    name: 'Orders API',
    slug: 'orders',
    description: 'Place and track orders.',
    status: 'published',
    has_openapi: true,
    openapi: DOC,
    routes: [{ path_prefix: '/v1/orders', methods: ['GET', 'POST'], auth_required: true }],
    updated_at: STAMP,
    ...overrides,
  }
}

function portalApp(overrides: Partial<Schema<'AppOut'>> = {}): Schema<'AppOut'> {
  return {
    id: 'app-1',
    name: 'My shop',
    status: 'approved',
    api: { id: 'a-1', name: 'Orders API', slug: 'orders', status: 'published' },
    plan: { id: 'p-1', name: 'Gold', rate_limit_per_min: 600, monthly_quota: 1000 },
    workspace: { slug: 'acme', name: 'Acme' },
    created_at: STAMP,
    ...overrides,
  }
}

function withPortal(options: { api?: Schema<'CatalogApi'>; app?: Schema<'AppOut'> } = {}) {
  const a = options.api ?? catalogApi()
  const app = options.app ?? portalApp()
  server.use(
    http.get(api(`${P}/me`), () =>
      json({ id: 'd-1', email: 'dev@example.com', status: 'active', created_at: STAMP }),
    ),
    http.get(api(`${P}/workspaces`), () => json(page([{ slug: 'acme', name: 'Acme', apis: 1 }]))),
    http.get(api(`${P}/workspaces/acme/apis`), () => {
      const summary: Partial<Schema<'CatalogApi'>> = { ...a }
      delete summary.openapi
      return json(page([summary]))
    }),
    http.get(api(`${P}/workspaces/acme/apis/:id`), ({ params }) =>
      params.id === a.id ? json(a) : detail(404, 'API not found'),
    ),
    http.get(api(`${P}/workspaces/acme/plans`), () =>
      json(
        page([
          {
            id: 'p-1',
            name: 'Gold',
            rate_limit_per_min: 600,
            monthly_quota: 1000,
            requires_approval: true,
          },
        ]),
      ),
    ),
    http.get(api(`${P}/apps`), () => json(page([app]))),
    http.get(api(`${P}/apps/:id`), ({ params }) =>
      params.id === app.id ? json(app) : detail(404, 'App not found'),
    ),
    http.get(api(`${P}/apps/:id/keys`), () => json(page([makeKey()]))),
    http.get(api(`${P}/apps/:id/usage`), () =>
      json({
        traffic: makeTraffic(),
        quota: { monthly_quota: 1000, used: 950, remaining: 50, resets_at: '2026-12-01T00:00:00Z' },
      }),
    ),
  )
}

describe('portal sign-in and sign-up', () => {
  it('sends a visitor to the portal sign-in and back', async () => {
    developerGoTrue.addUser('dev@example.com', 'correct-horse-battery')
    withPortal()
    const { user, router } = renderApp('/portal/apps')
    expect(
      await screen.findByRole('heading', { name: 'Sign in to the developer portal' }),
    ).toBeInTheDocument()
    expect(router.state.location.search).toBe('?next=%2Fportal%2Fapps')
    await user.type(screen.getByLabelText('Email'), 'dev@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('heading', { name: 'My apps' })).toBeInTheDocument()
  })

  it('signs up with a confirmation email that leads back to the portal', async () => {
    const { user } = renderApp('/portal/signup')
    await user.type(await screen.findByLabelText('Email'), 'new@example.com')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.type(screen.getByLabelText('Repeat the password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Sign up' }))
    expect(await screen.findByText('Use at least 12 characters')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('Password'))
    await user.clear(screen.getByLabelText('Repeat the password'))
    await user.type(screen.getByLabelText('Password'), 'a-long-password-1')
    await user.type(screen.getByLabelText('Repeat the password'), 'a-long-password-1')
    await user.click(screen.getByRole('button', { name: 'Sign up' }))
    expect(await screen.findByRole('status')).toHaveTextContent('new@example.com')
    expect(developerGoTrue.calls).toContain(
      `signup:new@example.com:${window.location.origin}/portal`,
    )
  })

  it('sends a recovery email and lets the developer choose a new password from its link', async () => {
    const { user, unmount } = renderApp('/portal/forgot-password')
    await user.type(await screen.findByLabelText('Email'), 'dev@example.com')
    await user.click(screen.getByRole('button', { name: 'Send the link' }))
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(developerGoTrue.calls).toContain('recover:dev@example.com')
    unmount()

    developerGoTrue.addUser('dev@example.com', 'old-password-123')
    developerGoTrue.storeSession('scratch', 'dev@example.com')
    const stored = JSON.parse(localStorage.getItem('scratch') ?? '{}') as {
      access_token: string
      refresh_token: string
    }
    localStorage.removeItem('scratch')
    window.history.replaceState(
      null,
      '',
      `/portal#access_token=${stored.access_token}&refresh_token=${stored.refresh_token}&type=recovery`,
    )
    withPortal()
    const second = renderApp('/portal')
    expect(
      await screen.findByRole('heading', { name: 'Choose a new password' }),
    ).toBeInTheDocument()
    await second.user.type(screen.getByLabelText('New password'), 'a-new-password-99')
    await second.user.type(screen.getByLabelText('Repeat the password'), 'a-new-password-99')
    await second.user.click(screen.getByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByRole('heading', { name: 'APIs' })).toBeInTheDocument()
  })

  it('tells a blocked developer why, and signs out', async () => {
    server.use(http.get(api(`${P}/me`), () => detail(403, 'developer_blocked')))
    const { user } = renderAsDeveloper('/portal')
    expect(await screen.findByText('Your developer account is blocked')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Account menu' }))
    await user.click(await screen.findByRole('menuitem', { name: /Sign out/ }))
    expect(
      await screen.findByRole('heading', { name: 'Sign in to the developer portal' }),
    ).toBeInTheDocument()
  })
})

describe('portal catalog and API pages', () => {
  it('lists the published APIs by workspace', async () => {
    withPortal()
    renderAsDeveloper('/portal')
    expect(await screen.findByRole('heading', { name: 'Acme' })).toBeInTheDocument()
    const card = await screen.findByRole('link', { name: /Orders API/ })
    expect(card).toHaveAttribute('href', '/portal/apis/acme/a-1')
    expect(within(card).getByText('1 route')).toBeInTheDocument()
    expect(within(card).getByText('Docs')).toBeInTheDocument()
  })

  it('says when nothing is published', async () => {
    withPortal()
    server.use(http.get(api(`${P}/workspaces`), () => json(page([]))))
    renderAsDeveloper('/portal')
    expect(await screen.findByText('No APIs are published yet')).toBeInTheDocument()
  })

  it('shows the docs and tries an operation through the gateway', async () => {
    withPortal()
    let seen: { url: string; key: string | null } | null = null
    server.use(
      http.get(`${window.location.origin}/try/acme/v1/orders/7`, ({ request }) => {
        seen = { url: request.url, key: request.headers.get('X-API-Key') }
        return HttpResponse.json({ id: 7 })
      }),
    )
    const { user } = renderAsDeveloper('/portal/apis/acme/a-1')
    expect(await screen.findByRole('heading', { name: 'Orders API' })).toBeInTheDocument()
    expect(screen.getByText('v2.1')).toBeInTheDocument()
    expect(screen.getByText('needs an API key')).toBeInTheDocument()

    await user.click(screen.getByText('One order'))
    expect(await screen.findByText('No such order')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try it →' }))
    const path = screen.getByLabelText('Path')
    expect(path).toHaveValue('/v1/orders/{id}')
    await user.clear(path)
    await user.type(path, '/v1/orders/7')
    await user.type(screen.getByLabelText('API key'), 'ak_live_test')
    await user.click(screen.getByRole('button', { name: /Send/ }))
    expect(await screen.findByLabelText('Response body')).toHaveTextContent('"id": 7')
    expect(seen).toEqual({
      url: `${window.location.origin}/try/acme/v1/orders/7`,
      key: 'ak_live_test',
    })
  })

  it('reports an unreachable gateway', async () => {
    withPortal({ api: catalogApi({ openapi: null, has_openapi: false }) })
    server.use(http.get(`${window.location.origin}/try/acme/v1/orders`, () => HttpResponse.error()))
    const { user } = renderAsDeveloper('/portal/apis/acme/a-1')
    expect(await screen.findByText('This API has no documentation yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Send/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The gateway could not be reached.')
  })

  it('creates an app that waits for approval', async () => {
    withPortal()
    let body: unknown
    server.use(
      http.post(api(`${P}/apps`), async ({ request }) => {
        body = await request.json()
        return json(portalApp({ id: 'app-1', status: 'pending' }), 201)
      }),
    )
    const { user, router } = renderAsDeveloper('/portal/apis/acme/a-1')
    await user.click(await screen.findByRole('button', { name: /Create an app/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Create app' }))
    expect(await screen.findByText('Name your app')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('App name'), 'My shop')
    await user.click(within(dialog).getByRole('combobox'))
    await user.click(
      await screen.findByRole('option', { name: /Gold: 600\/min, 1,000 a month · needs approval/ }),
    )
    await user.click(within(dialog).getByRole('button', { name: 'Create app' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/portal/apps/app-1')
    })
    expect(body).toEqual({ name: 'My shop', plan_id: 'p-1', workspace: 'acme', api_id: 'a-1' })
  })

  it('offers no new app for a deprecated API', async () => {
    withPortal({ api: catalogApi({ status: 'deprecated' }) })
    renderAsDeveloper('/portal/apis/acme/a-1')
    expect(await screen.findByText(/takes no new apps/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create an app/ })).not.toBeInTheDocument()
  })

  it('says when an API is not in the catalog', async () => {
    withPortal()
    renderAsDeveloper('/portal/apis/acme/nope')
    expect(await screen.findByText('API not found')).toBeInTheDocument()
  })
})

describe('portal apps', () => {
  it('lists my apps and opens one', async () => {
    withPortal()
    const { user, router } = renderAsDeveloper('/portal/apps')
    await user.click(await screen.findByRole('row', { name: 'Open app My shop' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/portal/apps/app-1')
    })
    // Its page loads before the test ends, so no request runs into the next test.
    expect(await screen.findByRole('heading', { name: 'My shop' })).toBeInTheDocument()
  })

  it('suggests the catalog when there are no apps', async () => {
    withPortal()
    server.use(http.get(api(`${P}/apps`), () => json(page([]))))
    renderAsDeveloper('/portal/apps')
    expect(await screen.findByRole('link', { name: 'Browse the APIs' })).toHaveAttribute(
      'href',
      '/portal',
    )
  })

  it('shows keys, usage and the quota, generates and revokes a key', async () => {
    withPortal()
    const calls: string[] = []
    server.use(
      http.post(api(`${P}/apps/app-1/keys`), async ({ request }) => {
        const { name } = (await request.json()) as { name: string }
        calls.push(`create ${name}`)
        return json({ ...makeKey({ name }), key: 'ak_live_portal_key' }, 201)
      }),
      http.delete(api(`${P}/apps/app-1/keys/:id`), ({ params }) => {
        calls.push(`revoke ${String(params.id)}`)
        return new Response(null, { status: 204 })
      }),
    )
    const { user } = renderAsDeveloper('/portal/apps/app-1')
    expect(
      await screen.findByText('950 of 1,000 requests · resets Dec 1, 2026'),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Monthly quota used' })).toHaveAttribute(
      'aria-valuenow',
      '95',
    )
    expect(screen.getByText('http://acme.localhost:8081/')).toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: /Generate key/ }))
    await user.type(await screen.findByLabelText('Name'), 'laptop')
    await user.click(screen.getByRole('button', { name: 'Generate' }))
    expect(await screen.findByLabelText('New API key')).toHaveTextContent('ak_live_portal_key')
    await user.click(screen.getByRole('button', { name: 'I copied it' }))

    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    await user.click(await screen.findByRole('button', { name: 'Revoke key' }))
    await screen.findByText('Key production revoked')
    expect(calls).toEqual(['create laptop', 'revoke k-1'])
  })

  it('reports a refused key', async () => {
    withPortal()
    server.use(http.post(api(`${P}/apps/app-1/keys`), () => detail(409, 'quota_exceeded')))
    const { user } = renderAsDeveloper('/portal/apps/app-1')
    await user.click(await screen.findByRole('button', { name: /Generate key/ }))
    await user.type(await screen.findByLabelText('Name'), 'eleventh')
    await user.click(screen.getByRole('button', { name: 'Generate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('reached its limit')
  })

  it('explains a pending or rejected app, without keys', async () => {
    withPortal({ app: portalApp({ status: 'pending' }) })
    const { unmount } = renderAsDeveloper('/portal/apps/app-1')
    expect(await screen.findByText('Waiting for approval')).toBeInTheDocument()
    expect(screen.getByText('Keys are available once the app is approved.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Generate key/ })).not.toBeInTheDocument()
    unmount()

    withPortal({
      app: portalApp({
        status: 'rejected',
        plan: { id: 'p-1', name: 'Free', rate_limit_per_min: 10, monthly_quota: null },
      }),
    })
    server.use(
      http.get(api(`${P}/apps/:id/usage`), () =>
        json({
          traffic: makeTraffic(0),
          quota: {
            monthly_quota: null,
            used: 3,
            remaining: null,
            resets_at: '2026-12-01T00:00:00Z',
          },
        }),
      ),
    )
    renderAsDeveloper('/portal/apps/app-1', 'other@example.com')
    expect(await screen.findByText('Rejected')).toBeInTheDocument()
    expect(await screen.findByText('3 requests · no monthly quota')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('deletes an app', async () => {
    withPortal()
    server.use(http.delete(api(`${P}/apps/app-1`), () => new Response(null, { status: 204 })))
    const { user, router } = renderAsDeveloper('/portal/apps/app-1')
    await user.click(await screen.findByRole('button', { name: /Delete app/ }))
    await user.click(await screen.findByRole('button', { name: 'Delete app' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/portal/apps')
    })
  })
})
