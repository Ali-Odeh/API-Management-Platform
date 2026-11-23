import { screen, waitFor, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole, makeConsumer, makeKey, makePlan, makeTraffic } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const CONSUMERS = '/api/v1/workspaces/acme/consumers'

function withConsumers(consumers = [makeConsumer()], keys = [makeKey()]) {
  const statuses: (string | null)[] = []
  server.use(
    http.get(api('/api/v1/workspaces/acme/plans'), () =>
      json(
        page([
          makePlan({
            id: 'p-2',
            name: 'Gold',
            is_default: false,
            rate_limit_per_min: 600,
            monthly_quota: 100000,
          }),
          makePlan(),
        ]),
      ),
    ),
    http.get(api(CONSUMERS), ({ request }) => {
      const status = new URL(request.url).searchParams.get('status')
      statuses.push(status)
      return json(page(status ? consumers.filter((c) => c.status === status) : consumers))
    }),
    http.get(api(`${CONSUMERS}/:id`), ({ params }) => {
      const found = consumers.find((c) => c.id === params.id)
      return found ? json(found) : detail(404, 'Consumer not found')
    }),
    http.get(api(`${CONSUMERS}/:id/keys`), () => json(page(keys))),
    http.get(api('/api/v1/workspaces/acme/analytics/traffic'), () => json(makeTraffic())),
  )
  return statuses
}

describe('consumers', () => {
  it('lists consumers with their plan and filters by status', async () => {
    const statuses = withConsumers([
      makeConsumer(),
      makeConsumer({ id: 'c-2', name: 'partner', status: 'blocked', plan_id: 'p-2' }),
    ])
    const { user } = renderSignedIn('/w/acme/consumers')
    expect(await screen.findByText('partner')).toBeInTheDocument()
    expect(await screen.findByText('Gold')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Blocked' }))
    await waitFor(() => {
      expect(screen.queryByText('mobile-app')).not.toBeInTheDocument()
    })
    expect(statuses).toContain('blocked')
  })

  it('creates a consumer on a chosen plan', async () => {
    withConsumers([])
    let body: unknown
    server.use(
      http.post(api(CONSUMERS), async ({ request }) => {
        body = await request.json()
        return json(makeConsumer({ name: 'shop' }), 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/consumers')
    await user.click(await screen.findByRole('button', { name: /New consumer/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Shop!')
    await user.type(within(dialog).getByLabelText('Contact email'), 'shop@example.test')
    await user.click(within(dialog).getByRole('button', { name: 'Create consumer' }))
    expect(await screen.findByText('Lowercase letters, digits, - and _ only')).toBeInTheDocument()

    await user.clear(within(dialog).getByLabelText('Name'))
    await user.type(within(dialog).getByLabelText('Name'), 'shop')
    await user.click(within(dialog).getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: /Gold/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Create consumer' }))
    expect(await screen.findByText('Consumer shop created')).toBeInTheDocument()
    expect(body).toEqual({ name: 'shop', email: 'shop@example.test', plan_id: 'p-2' })
  })

  it('shows a consumer with its keys and traffic, and blocks it', async () => {
    withConsumers(
      [makeConsumer()],
      [makeKey(), makeKey({ id: 'k-2', name: 'old', revoked_at: '2026-11-21T10:00:00Z' })],
    )
    let patched: unknown
    server.use(
      http.patch(api(`${CONSUMERS}/c-1`), async ({ request }) => {
        patched = await request.json()
        return json(makeConsumer({ status: 'blocked' }))
      }),
    )
    const { user } = renderSignedIn('/w/acme/consumers/c-1')
    expect(await screen.findByRole('heading', { name: 'mobile-app' })).toBeInTheDocument()
    expect(await screen.findAllByText('ak_live_3f9a…')).toHaveLength(2)
    expect(screen.getByText('revoked')).toBeInTheDocument()
    expect(await screen.findByRole('img', { name: 'Requests over time' })).toBeInTheDocument()
    expect(await screen.findByText(/Default \(60\/min\)/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Block/ }))
    expect(await screen.findByText('mobile-app blocked')).toBeInTheDocument()
    expect(patched).toEqual({ status: 'blocked' })
  })

  it('generates a key and shows it once', async () => {
    withConsumers([makeConsumer()], [])
    server.use(
      http.post(api(`${CONSUMERS}/c-1/keys`), async ({ request }) => {
        const { name } = (await request.json()) as { name: string }
        return json({ ...makeKey({ name }), key: 'ak_live_3f9a8c1d5e7b2a4f6c8d0e1f3a5b7c9d' }, 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/consumers/c-1')
    expect(await screen.findByText('No keys')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Generate key/ }))
    await user.type(await screen.findByLabelText('Name'), 'staging')
    await user.click(screen.getByRole('button', { name: 'Generate' }))
    expect(await screen.findByLabelText('New API key')).toHaveTextContent(
      'ak_live_3f9a8c1d5e7b2a4f6c8d0e1f3a5b7c9d',
    )
    expect(screen.getByText(/only time “staging” is shown/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'I copied it' }))
    await waitFor(() => {
      expect(screen.queryByLabelText('New API key')).not.toBeInTheDocument()
    })
  })

  it('reports a refused key and revokes a key', async () => {
    withConsumers()
    let revoked = ''
    server.use(
      http.post(api(`${CONSUMERS}/c-1/keys`), () => detail(409, 'quota_exceeded')),
      http.delete(api('/api/v1/workspaces/acme/keys/:id'), ({ params }) => {
        revoked = String(params.id)
        return new Response(null, { status: 204 })
      }),
    )
    const { user } = renderSignedIn('/w/acme/consumers/c-1')
    await user.click(await screen.findByRole('button', { name: /Generate key/ }))
    await user.type(await screen.findByLabelText('Name'), 'eleventh')
    await user.click(screen.getByRole('button', { name: 'Generate' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('reached its limit')
    await user.keyboard('{Escape}')

    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    await user.click(await screen.findByRole('button', { name: 'Revoke key' }))
    expect(await screen.findByText('Key production revoked')).toBeInTheDocument()
    expect(revoked).toBe('k-1')
  })

  it('edits, unblocks and deletes a consumer', async () => {
    withConsumers([makeConsumer({ status: 'blocked' })])
    const calls: unknown[] = []
    server.use(
      http.patch(api(`${CONSUMERS}/c-1`), async ({ request }) => {
        calls.push(await request.json())
        return json(makeConsumer())
      }),
      http.delete(api(`${CONSUMERS}/c-1`), () => new Response(null, { status: 204 })),
    )
    const { user, router } = renderSignedIn('/w/acme/consumers/c-1')
    expect(await screen.findByText('The gateway answers its keys with 403.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Unblock/ }))
    await screen.findByText('mobile-app unblocked')

    await user.click(screen.getByRole('button', { name: /Edit/ }))
    const email = await screen.findByLabelText('Contact email')
    await user.clear(email)
    await user.type(email, 'apps@example.test')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Consumer mobile-app saved')
    expect(calls).toEqual([
      { status: 'active' },
      { name: 'mobile-app', email: 'apps@example.test', plan_id: 'p-1' },
    ])

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(await screen.findByRole('button', { name: 'Delete consumer' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme/consumers')
    })
  })

  it('reports a refused block in words', async () => {
    withConsumers()
    server.use(http.patch(api(`${CONSUMERS}/c-1`), () => detail(403, 'workspace_suspended')))
    const { user } = renderSignedIn('/w/acme/consumers/c-1')
    await user.click(await screen.findByRole('button', { name: /Block/ }))
    expect(
      await screen.findByText('This workspace is suspended: it is read-only.'),
    ).toBeInTheDocument()
  })

  it('lets a viewer read keys but not change them', async () => {
    asRole('viewer')
    withConsumers()
    renderSignedIn('/w/acme/consumers/c-1')
    await screen.findByText('production')
    expect(screen.queryByRole('button', { name: /Generate key/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument()
  })
})
