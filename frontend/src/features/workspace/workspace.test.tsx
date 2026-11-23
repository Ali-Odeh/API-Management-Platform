import { screen } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { renderSignedIn } from '@/test/render'
import { api, detail, makeUser, makeWorkspaceDetail, server, state } from '@/test/server'

describe('workspace layout', () => {
  it('shows the workspace, my role and the sidebar', async () => {
    renderSignedIn('/w/acme')
    expect(await screen.findByRole('heading', { name: 'Acme' })).toBeInTheDocument()
    expect(screen.getAllByText('Owner').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: 'Overview' })[0]).toHaveAttribute('href', '/w/acme')
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('of 100 allowed')).toBeInTheDocument()
  })

  it('says a workspace was not found instead of failing', async () => {
    renderSignedIn('/w/nope')
    expect(await screen.findByText('Workspace not found')).toBeInTheDocument()
  })

  it('tells a Platform Admin without MFA to verify', async () => {
    state.me = makeUser({ is_platform_admin: true })
    state.myWorkspaces = []
    server.use(http.get(api('/api/v1/workspaces/:slug'), () => detail(403, 'mfa_required')))
    renderSignedIn('/w/acme')
    expect(await screen.findByText('Verify with MFA to open this workspace')).toBeInTheDocument()
  })

  it('shows a Platform Admin who is no member as such', async () => {
    state.me = makeUser({ is_platform_admin: true })
    state.myWorkspaces = []
    renderSignedIn('/w/acme', { aal: 'aal2', mfa: true })
    expect(await screen.findByRole('heading', { name: 'Acme' })).toBeInTheDocument()
    expect(screen.getByText('Platform Admin')).toBeInTheDocument()
  })

  it('warns that a suspended workspace is read-only', async () => {
    state.workspaces.set(
      'acme',
      makeWorkspaceDetail({ status: 'suspended', status_reason: 'Abuse report' }),
    )
    renderSignedIn('/w/acme')
    expect(await screen.findByText('This workspace is suspended')).toBeInTheDocument()
    expect(screen.getByText(/Abuse report/)).toBeInTheDocument()
  })

  it('offers a retry on other errors', async () => {
    server.use(http.get(api('/api/v1/workspaces/:slug'), () => detail(500, 'boom')))
    renderSignedIn('/w/acme')
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('answers unknown pages with a 404 page', async () => {
    renderSignedIn('/no/such/page')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
