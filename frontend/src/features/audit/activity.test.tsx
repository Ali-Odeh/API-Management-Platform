import { screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, json, page, server } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('activity', () => {
  it('lists events, filters them, and opens one', async () => {
    const queries: URLSearchParams[] = []
    server.use(
      http.get(api(`${W}/audit-events`), ({ request }) => {
        queries.push(new URL(request.url).searchParams)
        return json(
          page([
            {
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
              user_agent: 'Firefox',
              request_id: 'req-1',
              created_at: '2026-11-22T10:00:00Z',
            },
            {
              id: 'e-2',
              action: 'app.create',
              actor_type: 'developer',
              actor_email: null,
              actor_user_id: null,
              target_type: 'app',
              target_id: 'a-1',
              target_name: null,
              workspace_id: 'w-1',
              changes: {},
              ip: null,
              user_agent: null,
              request_id: null,
              created_at: '2026-11-22T09:00:00Z',
            },
          ]),
        )
      }),
    )
    const { user } = renderSignedIn('/w/acme/activity')
    expect(await screen.findByText('route.update')).toBeInTheDocument()
    expect(screen.getAllByText('Developer').length).toBeGreaterThan(0)

    await user.type(screen.getByLabelText('Actor email'), 'owner@example.test')
    await user.type(screen.getByLabelText('Action'), 'route.update')
    await user.type(screen.getByLabelText('From'), '2026-11-01')
    await user.type(screen.getByLabelText('To'), '2026-11-22')
    await user.click(screen.getByRole('button', { name: 'Filter' }))
    await waitFor(() => {
      expect(queries.at(-1)?.get('actor')).toBe('owner@example.test')
    })
    const last = queries.at(-1)
    expect(last?.get('action')).toBe('route.update')
    expect(last?.get('from')).toBeTruthy()
    expect(new Date(last?.get('to') ?? '') > new Date('2026-11-22T00:00:00')).toBe(true)

    // Clearing goes back to the unfiltered list (served from the cache here).
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByLabelText('Actor email')).toHaveValue('')

    await user.click(screen.getByRole('row', { name: /Details of route.update/ }))
    expect(await screen.findByText('Firefox')).toBeInTheDocument()
    expect(screen.getByText(/"POST"/)).toBeInTheDocument()
  })

  it('is hidden from a viewer', async () => {
    asRole('viewer')
    renderSignedIn('/w/acme')
    await screen.findByRole('heading', { name: 'Acme' })
    expect(screen.queryByRole('link', { name: 'Activity' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Logs' }).length).toBeGreaterThan(0)
  })
})
