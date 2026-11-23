import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole, makeApi, makeRoute } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const APIS = '/api/v1/workspaces/acme/apis'
const DOC = {
  openapi: '3.1.0',
  info: { title: 'Orders', version: '2.0' },
  paths: { '/a': {}, '/b': {} },
}

function withApi(item = makeApi()) {
  const patches: unknown[] = []
  server.use(
    http.get(api(APIS), () => json(page([item]))),
    http.get(api(`${APIS}/:id`), ({ params }) =>
      params.id === item.id ? json(item) : detail(404, 'API not found'),
    ),
    http.patch(api(`${APIS}/:id`), async ({ request }) => {
      patches.push(await request.json())
      return json(item)
    }),
    http.get(api('/api/v1/workspaces/acme/routes'), () =>
      json(page([makeRoute(), makeRoute({ id: 'r-2', path_prefix: '/v1/carts' })])),
    ),
  )
  return patches
}

describe('APIs', () => {
  it('lists APIs and creates one as a draft', async () => {
    withApi(makeApi({ has_openapi: true, status: 'published' }))
    let body: unknown
    server.use(
      http.post(api(APIS), async ({ request }) => {
        body = await request.json()
        return json(makeApi({ id: 'a-1' }), 201)
      }),
    )
    const { user, router } = renderSignedIn('/w/acme/apis')
    expect(await screen.findByText('Orders API')).toBeInTheDocument()
    expect(screen.getByLabelText('Has an OpenAPI document')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /New API/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Orders API')
    await user.type(within(dialog).getByLabelText('Slug'), 'orders')
    await user.click(within(dialog).getByRole('button', { name: 'Create API' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme/apis/a-1')
    })
    expect(body).toEqual({ name: 'Orders API', slug: 'orders', description: '' })
  })

  it('publishes a draft and later deprecates it', async () => {
    const patches = withApi()
    const { user } = renderSignedIn('/w/acme/apis/a-1')
    expect(await screen.findByText(/Only members see it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Deprecate/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Publish/ }))
    expect(await screen.findByText('Orders API is published')).toBeInTheDocument()
    expect(patches).toEqual([{ status: 'published' }])
  })

  it('offers deprecation on a published API and publishing again on a deprecated one', async () => {
    withApi(makeApi({ status: 'published' }))
    const { unmount } = renderSignedIn('/w/acme/apis/a-1')
    expect(await screen.findByRole('button', { name: /Deprecate/ })).toBeInTheDocument()
    unmount()
    withApi(makeApi({ status: 'deprecated' }))
    renderSignedIn('/w/acme/apis/a-1', { email: 'other@example.test' })
    expect(await screen.findByRole('button', { name: /Publish again/ })).toBeInTheDocument()
  })

  it('changes the routes of an API', async () => {
    const patches = withApi()
    const { user } = renderSignedIn('/w/acme/apis/a-1')
    const save = await screen.findByRole('button', { name: 'Save routes' })
    expect(save).toBeDisabled()
    await user.click(await screen.findByRole('checkbox', { name: /\/v1\/carts/ }))
    await user.click(screen.getByRole('checkbox', { name: /\/v1\/orders/ }))
    await user.click(save)
    expect(await screen.findByText('Routes saved')).toBeInTheDocument()
    expect(patches).toEqual([{ route_ids: ['r-2'] }])
  })

  it('reports a route that belongs to another API', async () => {
    withApi()
    server.use(http.patch(api(`${APIS}/:id`), () => detail(409, '/v1/carts is in the API Carts')))
    const { user } = renderSignedIn('/w/acme/apis/a-1')
    await user.click(await screen.findByRole('checkbox', { name: /\/v1\/carts/ }))
    await user.click(screen.getByRole('button', { name: 'Save routes' }))
    expect(await screen.findByText('/v1/carts is in the API Carts')).toBeInTheDocument()
  })

  it('saves a pasted or loaded document, refuses a broken one, and removes it', async () => {
    const patches = withApi(makeApi({ has_openapi: true, openapi: DOC }))
    const { user } = renderSignedIn('/w/acme/apis/a-1')
    expect(await screen.findByText('Orders')).toBeInTheDocument()
    expect(screen.getByText('· 2 paths')).toBeInTheDocument()

    const text = screen.getByLabelText(/paste the JSON/)
    fireEvent.change(text, { target: { value: '{"info": {}}' } })
    await user.click(screen.getByRole('button', { name: 'Save document' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('"openapi"')
    expect(patches).toEqual([])

    const file = new File([JSON.stringify(DOC)], 'orders.json', { type: 'application/json' })
    await user.upload(screen.getByLabelText(/Load a .json file/), file)
    await waitFor(() => {
      expect(text).toHaveValue(JSON.stringify(DOC))
    })
    await user.click(screen.getByRole('button', { name: 'Save document' }))
    expect(await screen.findByText('OpenAPI document saved')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await user.click(await screen.findByRole('button', { name: 'Remove document' }))
    expect(await screen.findByText('Document removed')).toBeInTheDocument()
    expect(patches).toEqual([{ openapi: DOC }, { openapi: null }])
  })

  it('edits and deletes an API', async () => {
    const patches = withApi()
    server.use(http.delete(api(`${APIS}/a-1`), () => new Response(null, { status: 204 })))
    const { user, router } = renderSignedIn('/w/acme/apis/a-1')
    await user.click(await screen.findByRole('button', { name: /Edit/ }))
    const description = await screen.findByLabelText('Description')
    await user.clear(description)
    await user.type(description, 'All about orders.')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('API saved')
    expect(patches).toEqual([{ name: 'Orders API', description: 'All about orders.' }])

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(await screen.findByRole('button', { name: 'Delete API' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme/apis')
    })
  })

  it('shows a viewer the API without controls', async () => {
    asRole('viewer')
    withApi(makeApi({ openapi: DOC, has_openapi: true }))
    renderSignedIn('/w/acme/apis/a-1')
    expect(await screen.findByText('Orders')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save routes' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/paste the JSON/)).not.toBeInTheDocument()
  })

  it('says when an API does not exist', async () => {
    withApi()
    renderSignedIn('/w/acme/apis/nope')
    expect(await screen.findByText('API not found')).toBeInTheDocument()
  })
})
