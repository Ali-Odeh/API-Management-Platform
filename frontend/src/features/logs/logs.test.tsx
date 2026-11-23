import { screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { makeConsumer, makeLog, makeRoute } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, json, page, server } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('logs', () => {
  it('filters the request log and opens an entry', async () => {
    const queries: URLSearchParams[] = []
    server.use(
      http.get(api(`${W}/routes`), () => json(page([makeRoute()]))),
      http.get(api(`${W}/consumers`), () => json(page([makeConsumer()]))),
      http.get(api(`${W}/request-logs`), ({ request }) => {
        queries.push(new URL(request.url).searchParams)
        return json(
          page([
            makeLog(),
            makeLog({ event_id: 'ev-2', status: 429, consumer_id: 'gone', route_id: null }),
          ]),
        )
      }),
    )
    const { user } = renderSignedIn('/w/acme/logs')
    expect(await screen.findByText('429')).toBeInTheDocument()
    expect(await screen.findByText('deleted consumer')).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Route' }))
    await user.click(await screen.findByRole('option', { name: '/v1/orders' }))
    await user.click(screen.getByRole('combobox', { name: 'Method' }))
    await user.click(await screen.findByRole('option', { name: 'POST' }))
    await user.type(screen.getByLabelText('Status'), '429{Enter}')
    await waitFor(() => {
      expect(queries.at(-1)?.get('status')).toBe('429')
    })
    expect(queries.at(-1)?.get('route_id')).toBe('r-1')
    expect(queries.at(-1)?.get('method')).toBe('POST')

    await user.click(screen.getByRole('button', { name: /Clear/ }))
    expect(screen.getByLabelText('Status')).toHaveValue('')

    await user.click(screen.getAllByRole('row', { name: /Details of GET/ })[0] as HTMLElement)
    expect(await screen.findByText('512 bytes')).toBeInTheDocument()
    expect(screen.getByText('10.0.0.9')).toBeInTheDocument()
  })
})
