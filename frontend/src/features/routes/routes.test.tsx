import { screen, waitFor, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole, makeRoute, makeService } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const ROUTES = '/api/v1/workspaces/acme/routes'

function withRoutes(routes = [makeRoute()]) {
  const seen: (string | null)[] = []
  server.use(
    http.get(api('/api/v1/workspaces/acme/services'), () =>
      json(page([makeService(), makeService({ id: 's-2', name: 'billing' })])),
    ),
    http.get(api(ROUTES), ({ request }) => {
      const serviceId = new URL(request.url).searchParams.get('service_id')
      seen.push(serviceId)
      return json(page(serviceId ? routes.filter((r) => r.service_id === serviceId) : routes))
    }),
  )
  return seen
}

describe('routes', () => {
  it('lists routes with their service, methods and CORS', async () => {
    withRoutes([
      makeRoute(),
      makeRoute({
        id: 'r-2',
        path_prefix: '/v1/invoices',
        service_id: 's-2',
        auth_required: false,
        cors: {
          origins: ['https://app.example.com'],
          methods: [],
          headers: [],
          credentials: false,
          max_age: 600,
        },
      }),
    ])
    renderSignedIn('/w/acme/routes')
    expect(await screen.findByText('/v1/invoices')).toBeInTheDocument()
    expect(screen.getAllByLabelText('API key required')).toHaveLength(1)
    expect(screen.getByText('https://app.example.com')).toBeInTheDocument()
    expect(screen.getByText('off')).toBeInTheDocument()
    expect(await screen.findByText('billing')).toBeInTheDocument()
  })

  it('filters by service', async () => {
    const seen = withRoutes([
      makeRoute(),
      makeRoute({ id: 'r-2', path_prefix: '/v1/invoices', service_id: 's-2' }),
    ])
    const { user } = renderSignedIn('/w/acme/routes')
    await screen.findByText('/v1/invoices')
    await user.click(screen.getByRole('combobox', { name: 'Filter by service' }))
    await user.click(await screen.findByRole('option', { name: 'billing' }))
    await waitFor(() => {
      expect(screen.queryByText('/v1/orders')).not.toBeInTheDocument()
    })
    expect(seen).toContain('s-2')
  })

  it('creates a route with CORS and a body limit', async () => {
    withRoutes([])
    let body: unknown
    server.use(
      http.post(api(ROUTES), async ({ request }) => {
        body = await request.json()
        return json(makeRoute({ path_prefix: '/v1/pay' }), 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/routes')
    await user.click(await screen.findByRole('button', { name: /New route/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Path prefix'), '/v1/pay')
    await user.click(within(dialog).getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'orders' }))
    const methods = within(dialog).getByRole('group', { name: 'Methods' })
    await user.click(within(methods).getByRole('checkbox', { name: 'POST' }))
    await user.type(within(dialog).getByLabelText('Body size limit (bytes)'), '1048576')
    await user.type(within(dialog).getByLabelText('Allowed origins'), 'https://shop.example.com')
    await user.type(within(dialog).getByLabelText('Allowed headers'), 'Content-Type, X-API-Key')
    await user.click(within(dialog).getByRole('button', { name: 'Create route' }))

    expect(await screen.findByText('Route /v1/pay created')).toBeInTheDocument()
    expect(body).toEqual({
      path_prefix: '/v1/pay',
      service_id: 's-1',
      methods: ['GET', 'POST'],
      strip_prefix: false,
      auth_required: true,
      is_active: true,
      max_body_bytes: 1048576,
      cors: {
        origins: ['https://shop.example.com'],
        methods: [],
        headers: ['Content-Type', 'X-API-Key'],
        credentials: false,
        max_age: 600,
      },
    })
  })

  it('refuses credentials with the origin * before saving', async () => {
    withRoutes()
    const { user } = renderSignedIn('/w/acme/routes')
    await user.click(await screen.findByRole('button', { name: 'Edit /v1/orders' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Allowed origins'), '*')
    await user.click(within(dialog).getByRole('switch', { name: 'Allow credentials' }))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText('Browsers refuse credentials with the origin *'),
    ).toBeInTheDocument()
  })

  it('edits a route back to the default body limit', async () => {
    withRoutes([makeRoute({ max_body_bytes: 2048 })])
    let body: Record<string, unknown> = {}
    server.use(
      http.patch(api(`${ROUTES}/r-1`), async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return json(makeRoute())
      }),
    )
    const { user } = renderSignedIn('/w/acme/routes')
    await user.click(await screen.findByRole('button', { name: 'Edit /v1/orders' }))
    const limit = await screen.findByLabelText('Body size limit (bytes)')
    expect(limit).toHaveValue(2048)
    await user.clear(limit)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Route /v1/orders saved')
    expect(body.max_body_bytes).toBeNull()
  })

  it('deletes a route, and shows a refusal', async () => {
    withRoutes()
    let calls = 0
    server.use(
      http.delete(api(`${ROUTES}/r-1`), () => {
        calls += 1
        return calls === 1
          ? detail(409, 'Route is part of the API Orders')
          : new Response(null, { status: 204 })
      }),
    )
    const { user } = renderSignedIn('/w/acme/routes')
    await user.click(await screen.findByRole('button', { name: 'Delete /v1/orders' }))
    await user.click(await screen.findByRole('button', { name: 'Delete route' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Route is part of the API Orders')
    await user.click(screen.getByRole('button', { name: 'Delete route' }))
    expect(await screen.findByText('Route /v1/orders deleted')).toBeInTheDocument()
  })

  it('gives a viewer no controls', async () => {
    asRole('viewer')
    withRoutes()
    renderSignedIn('/w/acme/routes')
    await screen.findByText('/v1/orders')
    expect(screen.queryByRole('button', { name: /New route/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit /v1/orders' })).not.toBeInTheDocument()
  })

  it('asks for a service first when there is none', async () => {
    server.use(
      http.get(api('/api/v1/workspaces/acme/services'), () => json(page([]))),
      http.get(api(ROUTES), () => json(page([]))),
    )
    renderSignedIn('/w/acme/routes')
    expect(
      await screen.findByText('Add a service first: every route forwards to one.'),
    ).toBeInTheDocument()
  })
})
