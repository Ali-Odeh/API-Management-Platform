import { screen, waitFor, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole, makeRoute, makeService, makeTarget } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const SERVICES = '/api/v1/workspaces/acme/services'

function withServices(services = [makeService()]) {
  server.use(
    http.get(api(SERVICES), () => json(page(services))),
    http.get(api(`${SERVICES}/:id`), ({ params }) => {
      const found = services.find((s) => s.id === params.id)
      return found ? json(found) : detail(404, 'Service not found')
    }),
    http.get(api('/api/v1/workspaces/acme/routes'), () => json(page([makeRoute()]))),
  )
}

describe('services', () => {
  it('lists services and opens one', async () => {
    withServices([
      makeService(),
      makeService({
        id: 's-2',
        name: 'billing',
        is_active: false,
        targets: [makeTarget(), makeTarget({ id: 't-2' })],
      }),
    ])
    const { user, router } = renderSignedIn('/w/acme/services')
    expect(await screen.findByText('orders')).toBeInTheDocument()
    expect(screen.getByText('inactive')).toBeInTheDocument()
    expect(screen.getByText(/\+1/)).toBeInTheDocument()
    await user.click(screen.getByRole('row', { name: 'Open service billing' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme/services/s-2')
    })
  })

  it('creates a service with its targets', async () => {
    withServices([])
    let body: unknown
    server.use(
      http.post(api(SERVICES), async ({ request }) => {
        body = await request.json()
        return json(makeService({ name: 'payments' }), 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/services')
    await user.click(
      (await screen.findAllByRole('button', { name: /New service/ }))[0] as HTMLElement,
    )

    await user.click(screen.getByRole('button', { name: 'Create service' }))
    expect(await screen.findByText('Enter a name')).toBeInTheDocument()
    expect(screen.getByText(/A full URL/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Name'), 'payments')
    await user.type(screen.getByLabelText('Target 1 URL'), 'http://pay-1:9000')
    await user.click(screen.getByRole('button', { name: /Add target/ }))
    await user.type(screen.getByLabelText('Target 2 URL'), 'http://pay-2:9000')
    await user.clear(screen.getByLabelText('Target 2 weight'))
    await user.type(screen.getByLabelText('Target 2 weight'), '50')
    await user.click(screen.getByRole('button', { name: /Show health checks/ }))
    await user.clear(screen.getByLabelText('Health check path'))
    await user.type(screen.getByLabelText('Health check path'), '/ready')
    await user.click(screen.getByRole('button', { name: 'Create service' }))

    expect(await screen.findByText('Service payments created')).toBeInTheDocument()
    expect(body).toMatchObject({
      name: 'payments',
      hc_path: '/ready',
      targets: [
        { url: 'http://pay-1:9000', weight: 100 },
        { url: 'http://pay-2:9000', weight: 50 },
      ],
    })
  })

  it('puts a refused field under the field', async () => {
    withServices([])
    server.use(
      http.post(api(SERVICES), () =>
        json(
          {
            detail: [
              {
                loc: ['body', 'targets', 0, 'url'],
                msg: 'Value error, internal address',
                type: 'value_error',
              },
            ],
          },
          422,
        ),
      ),
    )
    const { user } = renderSignedIn('/w/acme/services')
    await user.click(
      (await screen.findAllByRole('button', { name: /New service/ }))[0] as HTMLElement,
    )
    await user.type(screen.getByLabelText('Name'), 'local')
    await user.type(screen.getByLabelText('Target 1 URL'), 'http://localhost:9000')
    await user.click(screen.getByRole('button', { name: 'Create service' }))
    expect(await screen.findByText('internal address')).toBeInTheDocument()
  })

  it('shows a viewer no way to change services', async () => {
    asRole('viewer')
    withServices()
    renderSignedIn('/w/acme/services/s-1')
    expect(await screen.findByRole('heading', { name: 'orders' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add target/ })).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'http://orders:9000 active' })).toBeDisabled()
  })

  it('shows settings, routes and targets of a service', async () => {
    withServices()
    renderSignedIn('/w/acme/services/s-1')
    expect(await screen.findByText(/GET \/health every 10 s/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Routes to this service' })).toBeInTheDocument()
    expect(await screen.findByText('/v1/orders')).toBeInTheDocument()
  })

  it('adds a target, changes a weight, switches one off and removes one', async () => {
    const service = makeService({
      targets: [makeTarget(), makeTarget({ id: 't-2', url: 'http://orders-2:9000' })],
    })
    withServices([service])
    const calls: string[] = []
    server.use(
      http.post(api(`${SERVICES}/s-1/targets`), async ({ request }) => {
        calls.push(`add ${JSON.stringify(await request.json())}`)
        return json(makeTarget({ id: 't-3' }), 201)
      }),
      http.patch(api(`${SERVICES}/s-1/targets/:tid`), async ({ request, params }) => {
        calls.push(`patch ${String(params.tid)} ${JSON.stringify(await request.json())}`)
        return json(makeTarget())
      }),
      http.delete(api(`${SERVICES}/s-1/targets/:tid`), ({ params }) => {
        calls.push(`delete ${String(params.tid)}`)
        return new Response(null, { status: 204 })
      }),
    )
    const { user } = renderSignedIn('/w/acme/services/s-1')

    await user.click(await screen.findByRole('button', { name: /Add target/ }))
    const add = await screen.findByRole('dialog')
    await user.type(within(add).getByLabelText('URL'), 'http://orders-3:9000')
    await user.click(within(add).getByRole('button', { name: 'Add target' }))
    await screen.findByText('Target added')

    await user.click(screen.getByRole('button', { name: 'Change weight of http://orders-2:9000' }))
    const weight = within(await screen.findByRole('dialog')).getByLabelText('Weight')
    await user.clear(weight)
    await user.type(weight, '300')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Weight changed')

    await user.click(screen.getByRole('switch', { name: 'http://orders-2:9000 active' }))
    await screen.findByText('http://orders-2:9000 off')

    await user.click(screen.getByRole('button', { name: 'Remove http://orders-2:9000' }))
    await user.click(await screen.findByRole('button', { name: 'Remove target' }))
    await waitFor(() => {
      expect(calls).toContain('delete t-2')
    })
    expect(calls).toEqual([
      'add {"url":"http://orders-3:9000","weight":100}',
      'patch t-2 {"weight":300}',
      'patch t-2 {"is_active":false}',
      'delete t-2',
    ])
  })

  it('reports a refused target switch', async () => {
    withServices()
    server.use(
      http.patch(api(`${SERVICES}/s-1/targets/:tid`), () => detail(409, 'The last active target')),
    )
    const { user } = renderSignedIn('/w/acme/services/s-1')
    await user.click(await screen.findByRole('switch', { name: 'http://orders:9000 active' }))
    expect(await screen.findByText('The last active target')).toBeInTheDocument()
  })

  it('edits and deletes a service', async () => {
    withServices()
    let patched: unknown
    server.use(
      http.patch(api(`${SERVICES}/s-1`), async ({ request }) => {
        patched = await request.json()
        return json(makeService({ timeout_ms: 2000 }))
      }),
      http.delete(api(`${SERVICES}/s-1`), () => new Response(null, { status: 204 })),
    )
    const { user, router } = renderSignedIn('/w/acme/services/s-1')
    await user.click(await screen.findByRole('button', { name: /Edit/ }))
    const timeout = await screen.findByLabelText('Timeout (ms)')
    await user.clear(timeout)
    await user.type(timeout, '2000')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Service saved')
    expect(patched).toMatchObject({ name: 'orders', timeout_ms: 2000 })

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(await screen.findByRole('button', { name: 'Delete service' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme/services')
    })
  })

  it('says when a service does not exist', async () => {
    withServices([])
    renderSignedIn('/w/acme/services/nope')
    expect(await screen.findByText('Service not found')).toBeInTheDocument()
  })
})
