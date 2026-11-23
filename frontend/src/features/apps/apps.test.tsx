import { screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { makeApp } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

describe('apps', () => {
  it('shows the approval queue and approves or rejects', async () => {
    const asked: (string | null)[] = []
    const decisions: unknown[] = []
    server.use(
      http.get(api('/api/v1/workspaces/acme/apps'), ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        asked.push(status)
        return json(
          page(
            status === 'approved' ? [makeApp({ id: 'app-2', status: 'approved' })] : [makeApp()],
          ),
        )
      }),
      http.patch(api('/api/v1/workspaces/acme/apps/:id'), async ({ request, params }) => {
        decisions.push([params.id, await request.json()])
        return json(makeApp({ status: 'approved' }))
      }),
    )
    const { user } = renderSignedIn('/w/acme/apps')
    expect(await screen.findByText('Shop frontend')).toBeInTheDocument()
    expect(screen.getByText('dev@example.test')).toBeInTheDocument()
    expect(screen.getByText('Gold · 600/min')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Approve/ }))
    expect(await screen.findByText('Shop frontend approved')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Approved' }))
    await waitFor(() => {
      expect(asked).toContain('approved')
    })
    await user.click(await screen.findByRole('button', { name: /Reject/ }))
    await screen.findByText('Shop frontend rejected')
    expect(decisions).toEqual([
      ['app-1', { status: 'approved' }],
      ['app-2', { status: 'rejected' }],
    ])
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
  })

  it('says when nothing is waiting, and reports a refused decision', async () => {
    let empty = true
    server.use(
      http.get(api('/api/v1/workspaces/acme/apps'), () => json(page(empty ? [] : [makeApp()]))),
      http.patch(api('/api/v1/workspaces/acme/apps/:id'), () => detail(403, 'workspace_suspended')),
    )
    const { user, unmount } = renderSignedIn('/w/acme/apps')
    expect(await screen.findByText('Nothing is waiting')).toBeInTheDocument()
    unmount()
    empty = false
    renderSignedIn('/w/acme/apps', { email: 'second@example.test' })
    await user.click(await screen.findByRole('button', { name: /Approve/ }))
    expect(
      await screen.findByText('This workspace is suspended: it is read-only.'),
    ).toBeInTheDocument()
  })
})
