import { screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, makeWorkspaceDetail, server, state } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('settings', () => {
  it('renames the workspace and shows its limits', async () => {
    state.workspaces.set(
      'acme',
      makeWorkspaceDetail({
        limit_overrides: { ...makeWorkspaceDetail().limit_overrides, rate_limit_per_min: 6000 },
      }),
    )
    let body: unknown
    server.use(
      http.patch(api(W), async ({ request }) => {
        body = await request.json()
        return json(makeWorkspaceDetail({ name: 'Acme Inc' }))
      }),
    )
    const { user } = renderSignedIn('/w/acme/settings')
    expect(await screen.findByText('Requests per minute')).toBeInTheDocument()
    expect(screen.getByText('Custom')).toBeInTheDocument()
    const name = screen.getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Acme Inc')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Workspace renamed')).toBeInTheDocument()
    expect(body).toEqual({ name: 'Acme Inc' })
  })

  it('deletes the workspace after the slug is typed', async () => {
    let body: unknown
    server.use(
      http.delete(api(W), async ({ request }) => {
        body = await request.json()
        return new Response(null, { status: 204 })
      }),
    )
    const { user, router } = renderSignedIn('/w/acme/settings')
    await user.click(await screen.findByRole('button', { name: /Delete workspace/ }))
    const confirm = await screen.findByRole('button', { name: 'Delete workspace' })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/to confirm/), 'acme')
    await user.click(confirm)
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
    expect(body).toEqual({ confirm: 'acme' })
  })

  it('shows an API Admin the limits read-only and no delete', async () => {
    asRole('api_admin')
    server.use(http.patch(api(W), () => detail(500, 'boom')))
    renderSignedIn('/w/acme/settings')
    expect(await screen.findByLabelText('Name')).toBeDisabled()
    expect(screen.queryByText('Delete this workspace')).not.toBeInTheDocument()
  })

  it('reports a refused rename', async () => {
    server.use(http.patch(api(W), () => detail(403, 'workspace_suspended')))
    const { user } = renderSignedIn('/w/acme/settings')
    await user.type(await screen.findByLabelText('Name'), ' 2')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText('This workspace is suspended: it is read-only.'),
    ).toBeInTheDocument()
  })
})
