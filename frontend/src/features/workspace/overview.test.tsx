import { screen } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { makeLog, makeTraffic } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, json, page, server } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('overview', () => {
  it('shows traffic, the busiest routes and consumers, errors and quotas', async () => {
    server.use(
      http.get(api(`${W}/analytics/traffic`), () => json(makeTraffic(120))),
      http.get(api(`${W}/analytics/routes`), () =>
        json({
          start: 'x',
          end: 'x',
          items: [
            {
              route_id: 'r-1',
              path_prefix: '/v1/orders',
              requests: 100,
              client_errors: 4,
              server_errors: 1,
              latency: { p50_ms: 10, p95_ms: 30, p99_ms: 50 },
            },
            {
              route_id: null,
              path_prefix: null,
              requests: 20,
              client_errors: 20,
              server_errors: 0,
              latency: { p50_ms: 1, p95_ms: 2, p99_ms: 3 },
            },
          ],
        }),
      ),
      http.get(api(`${W}/analytics/consumers`), () =>
        json({
          start: 'x',
          end: 'x',
          items: [
            {
              consumer_id: 'c-1',
              name: 'mobile-app',
              requests: 90,
              client_errors: 0,
              server_errors: 0,
              latency: { p50_ms: 5, p95_ms: 9, p99_ms: 9 },
            },
            {
              consumer_id: null,
              name: null,
              requests: 30,
              client_errors: 30,
              server_errors: 0,
              latency: { p50_ms: 1, p95_ms: 1, p99_ms: 1 },
            },
          ],
        }),
      ),
      http.get(api(`${W}/request-logs`), () =>
        json(
          page([
            makeLog({ event_id: 'a', status: 502, path: '/v1/orders/1' }),
            makeLog({ event_id: 'b' }),
          ]),
        ),
      ),
    )
    const { user } = renderSignedIn('/w/acme')
    expect(await screen.findByText('120')).toBeInTheDocument()
    expect(screen.getByText('6.7%')).toBeInTheDocument()
    expect(screen.getByText('48 ms')).toBeInTheDocument()
    expect(await screen.findByText('no route (404)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'mobile-app' })).toHaveAttribute(
      'href',
      '/w/acme/consumers/c-1',
    )
    expect(screen.getByText('no key (public or refused)')).toBeInTheDocument()
    expect(await screen.findByText('502')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument() // one active consumer

    await user.click(screen.getByRole('combobox', { name: 'Period' }))
    await user.click(await screen.findByRole('option', { name: 'Last 7 days' }))
    expect(screen.getByRole('combobox', { name: 'Period' })).toHaveTextContent('Last 7 days')
  })
})
