import { screen, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { renderSignedIn } from '@/test/render'
import { api, detail, makeMyWorkspace, makeUser, server, state } from '@/test/server'

describe('my workspaces', () => {
  it('lists my workspaces with my role and status', async () => {
    state.myWorkspaces = [
      makeMyWorkspace(),
      makeMyWorkspace({
        slug: 'globex',
        name: 'Globex',
        role: 'viewer',
        status: 'suspended',
        status_reason: 'Unpaid invoice',
      }),
    ]
    renderSignedIn('/')
    const acme = await screen.findByText('Acme')
    const card = acme.closest('[data-slot="card"]') as HTMLElement
    expect(within(card).getByText('Owner')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: /Open/ })).toHaveAttribute('href', '/w/acme')

    expect(screen.getByText('Viewer')).toBeInTheDocument()
    expect(screen.getByText('suspended')).toBeInTheDocument()
    expect(screen.getByText('Unpaid invoice')).toBeInTheDocument()
  })

  it('explains how to get a workspace when there is none', async () => {
    state.myWorkspaces = []
    renderSignedIn('/')
    expect(await screen.findByText('No workspaces yet')).toBeInTheDocument()
    expect(screen.getByText(/An owner of a workspace can add you/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Platform/ })).not.toBeInTheDocument()
  })

  it('shows the Platform links to the Platform Admin', async () => {
    state.me = makeUser({ is_platform_admin: true })
    state.myWorkspaces = []
    renderSignedIn('/')
    expect(await screen.findByText('Create one on the Platform pages.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /Platform/ }).length).toBeGreaterThan(0)
  })

  it('leaves a workspace after confirming', async () => {
    let left = ''
    server.use(
      http.delete(api('/api/v1/me/workspaces/:slug'), ({ params }) => {
        left = String(params.slug)
        state.myWorkspaces = []
        return new Response(null, { status: 204 })
      }),
    )
    const { user } = renderSignedIn('/')
    await user.click(await screen.findByRole('button', { name: /Leave/ }))
    await user.click(await screen.findByRole('button', { name: 'Leave workspace' }))
    expect(await screen.findByText('No workspaces yet')).toBeInTheDocument()
    expect(left).toBe('acme')
  })

  it('shows why leaving failed, in words', async () => {
    server.use(http.delete(api('/api/v1/me/workspaces/:slug'), () => detail(409, 'last_owner')))
    const { user } = renderSignedIn('/')
    await user.click(await screen.findByRole('button', { name: /Leave/ }))
    await user.click(await screen.findByRole('button', { name: 'Leave workspace' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('must keep at least one owner')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('offers to try again when the list fails', async () => {
    server.use(http.get(api('/api/v1/me/workspaces'), () => detail(500, 'boom')))
    renderSignedIn('/')
    expect(await screen.findByText('Could not load this')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('switches workspaces from the top bar', async () => {
    state.myWorkspaces = [makeMyWorkspace(), makeMyWorkspace({ slug: 'globex', name: 'Globex' })]
    const { user, router } = renderSignedIn('/')
    await user.click(await screen.findByRole('button', { name: 'Select a workspace' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Globex' }))
    expect(router.state.location.pathname).toBe('/w/globex')
  })

  it('changes the theme', async () => {
    const { user } = renderSignedIn('/')
    await user.click(await screen.findByRole('button', { name: 'Theme' }))
    await user.click(await screen.findByRole('menuitemradio', { name: 'Dark' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
    await user.click(screen.getByRole('button', { name: 'Theme' }))
    await user.click(await screen.findByRole('menuitemradio', { name: 'System' }))
    expect(document.documentElement).not.toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBeNull()
  })
})
