import { screen, waitFor, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import type { Schema } from '@/api/types'
import { makeEvent } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import {
  api,
  detail,
  json,
  makeUser,
  makeWorkspaceDetail,
  page,
  server,
  state,
} from '@/test/server'
import { limitChanges } from './limits'

const STAMP = '2026-11-20T10:00:00Z'

function workspace(overrides: Partial<Schema<'WorkspaceOut'>> = {}): Schema<'WorkspaceOut'> {
  const detailFields: Partial<Schema<'WorkspaceDetail'>> = { ...makeWorkspaceDetail() }
  delete detailFields.usage
  return { ...(detailFields as Schema<'WorkspaceOut'>), ...overrides }
}

function asPlatformAdmin(path: string, options: { aal?: 'aal1' | 'aal2'; mfa?: boolean } = {}) {
  state.me = makeUser({ is_platform_admin: true })
  return renderSignedIn(path, { aal: options.aal ?? 'aal2', mfa: options.mfa ?? true })
}

describe('platform guard', () => {
  it('keeps out anyone but the Platform Admin', async () => {
    renderSignedIn('/platform/workspaces')
    expect(
      await screen.findByText('Only the Platform Admin can open these pages'),
    ).toBeInTheDocument()
  })

  it('asks a Platform Admin without an authenticator to set one up', async () => {
    asPlatformAdmin('/platform', { aal: 'aal1', mfa: false })
    expect(await screen.findByRole('link', { name: /Set up an authenticator/ })).toHaveAttribute(
      'href',
      '/account',
    )
  })

  it('asks a Platform Admin with an authenticator for the code first', async () => {
    asPlatformAdmin('/platform', { aal: 'aal1', mfa: true })
    expect(await screen.findByLabelText('Authentication code')).toBeInTheDocument()
  })
})

describe('platform workspaces', () => {
  function withWorkspaces(items: Schema<'WorkspaceOut'>[]) {
    const calls: string[] = []
    server.use(
      http.get(api('/api/v1/workspaces'), ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        return json(page(status ? items.filter((w) => w.status === status) : items))
      }),
      http.post(api('/api/v1/workspaces'), async ({ request }) => {
        calls.push(`create ${JSON.stringify(await request.json())}`)
        return json(workspace({ slug: 'globex', name: 'Globex' }), 201)
      }),
      http.post(api('/api/v1/workspaces/:slug/:action'), async ({ request, params }) => {
        const body = request.headers.get('content-type') ? JSON.stringify(await request.json()) : ''
        calls.push(`${String(params.action)} ${String(params.slug)} ${body}`.trim())
        return json(workspace())
      }),
      http.delete(api('/api/v1/workspaces/:slug'), async ({ request, params }) => {
        calls.push(`delete ${String(params.slug)} ${JSON.stringify(await request.json())}`)
        return new Response(null, { status: 204 })
      }),
      http.patch(api('/api/v1/workspaces/:slug/limits'), async ({ request, params }) => {
        calls.push(`limits ${String(params.slug)} ${JSON.stringify(await request.json())}`)
        return json(workspace())
      }),
    )
    return calls
  }

  it('lists workspaces with their status and creates one', async () => {
    const calls = withWorkspaces([
      workspace(),
      workspace({
        id: 'w-2',
        slug: 'old',
        name: 'Old',
        status: 'deleted',
        purge_after: '2026-12-20T10:00:00Z',
      }),
      workspace({
        id: 'w-3',
        slug: 'paused',
        name: 'Paused',
        status: 'suspended',
        status_reason: 'Unpaid',
      }),
    ])
    const { user } = asPlatformAdmin('/platform')
    expect(await screen.findByText('Restorable until Dec 20, 2026')).toBeInTheDocument()
    expect(screen.getByText('Unpaid')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /New workspace/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Globex')
    await user.type(within(dialog).getByLabelText('Slug'), 'Globex!')
    await user.type(within(dialog).getByLabelText("First owner's email"), 'ceo@globex.com')
    await user.click(within(dialog).getByRole('button', { name: 'Create workspace' }))
    expect(
      await screen.findByText('Lowercase letters, digits and inner dashes'),
    ).toBeInTheDocument()
    await user.clear(within(dialog).getByLabelText('Slug'))
    await user.type(within(dialog).getByLabelText('Slug'), 'globex')
    await user.click(within(dialog).getByRole('button', { name: 'Create workspace' }))
    expect(await screen.findByText(/Workspace Globex created/)).toBeInTheDocument()
    expect(calls).toEqual([
      'create {"name":"Globex","slug":"globex","owner_email":"ceo@globex.com"}',
    ])
  })

  it('suspends, unsuspends, restores and deletes', async () => {
    const calls = withWorkspaces([
      workspace(),
      workspace({ id: 'w-2', slug: 'old', name: 'Old', status: 'deleted' }),
      workspace({ id: 'w-3', slug: 'paused', name: 'Paused', status: 'suspended' }),
    ])
    const { user } = asPlatformAdmin('/platform/workspaces')

    await user.click(await screen.findByRole('button', { name: 'Actions for acme' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Suspend' }))
    await user.type(await screen.findByLabelText('Reason (members see it)'), 'Abuse report')
    await user.click(screen.getByRole('button', { name: 'Suspend' }))
    await screen.findByText('Acme suspended')

    await user.click(screen.getByRole('button', { name: 'Actions for paused' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Unsuspend' }))
    await screen.findByText('Paused is active again')

    await user.click(screen.getByRole('button', { name: 'Actions for old' }))
    expect(screen.queryByRole('menuitem', { name: 'Open dashboard' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('menuitem', { name: 'Restore' }))
    await screen.findByText('Old restored')

    await user.click(screen.getByRole('button', { name: 'Actions for acme' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    await user.type(await screen.findByLabelText(/to confirm/), 'acme')
    await user.click(screen.getByRole('button', { name: 'Delete workspace' }))
    await screen.findByText('Acme deleted')

    expect(calls).toEqual([
      'suspend acme {"reason":"Abuse report"}',
      'unsuspend paused',
      'restore old',
      'delete acme {"confirm":"acme"}',
    ])
  })

  it('reports a refused transition', async () => {
    withWorkspaces([workspace({ status: 'deleted' })])
    server.use(
      http.post(api('/api/v1/workspaces/:slug/restore'), () => detail(409, 'restore_period_over')),
    )
    const { user } = asPlatformAdmin('/platform/workspaces')
    await user.click(await screen.findByRole('button', { name: 'Actions for acme' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Restore' }))
    expect(
      await screen.findByText('The restore period of this workspace is over.'),
    ).toBeInTheDocument()
  })

  it('changes limits, sending only what changed', async () => {
    const calls = withWorkspaces([
      workspace({ limit_overrides: { ...workspace().limit_overrides, max_routes: 50 } }),
    ])
    const { user, router } = asPlatformAdmin('/platform/workspaces')
    await user.click(await screen.findByRole('button', { name: 'Actions for acme' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Change limits' }))
    const rate = await screen.findByLabelText('Requests per minute')
    expect(rate).toHaveAttribute('placeholder', '6,000 (default)')
    await user.type(rate, '0')
    await user.click(screen.getByRole('button', { name: 'Save limits' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Requests per minute: a whole number',
    )
    await user.clear(rate)
    await user.type(rate, '100')
    await user.clear(screen.getByLabelText('Routes'))
    await user.click(screen.getByRole('button', { name: 'Save limits' }))
    await screen.findByText('Limits of Acme saved')
    expect(calls).toEqual(['limits acme {"rate_limit_per_min":100,"max_routes":null}'])

    await user.click(screen.getByRole('button', { name: 'Actions for acme' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Open dashboard' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/w/acme')
    })
  })

  it('closes the limits dialog without a call when nothing changed', () => {
    const ws = workspace()
    const draft = {
      rate_limit_per_min: '',
      max_concurrent: '',
      ip_rate_limit_per_min: '',
      max_services: '',
      max_routes: '',
      max_consumers: '',
    }
    expect(limitChanges(ws, draft)).toEqual({ body: {} })
    expect(limitChanges(ws, { ...draft, max_services: '1.5' })).toHaveProperty('error')
  })
})

describe('platform users', () => {
  const users: Schema<'UserListItem'>[] = [
    {
      id: 'u-1',
      email: 'owner@example.test',
      is_platform_admin: true,
      is_active: true,
      created_at: STAMP,
      workspaces: [],
    },
    {
      id: 'u-2',
      email: 'ana@example.com',
      is_platform_admin: false,
      is_active: true,
      created_at: STAMP,
      workspaces: [
        { slug: 'acme', name: 'Acme', role: 'owner', status: 'active' },
        { slug: 'old', name: 'Old', role: 'viewer', status: 'deleted' },
      ],
    },
    {
      id: 'u-3',
      email: 'bo@example.com',
      is_platform_admin: false,
      is_active: false,
      created_at: STAMP,
      workspaces: [],
    },
  ]

  it('lists people with their workspaces, filters, and disables one', async () => {
    const queries: URLSearchParams[] = []
    let patched: unknown
    server.use(
      http.get(api('/api/v1/users'), ({ request }) => {
        queries.push(new URL(request.url).searchParams)
        return json(page(users))
      }),
      http.patch(api('/api/v1/users/:id'), async ({ request }) => {
        patched = await request.json()
        return json(makeUser())
      }),
    )
    const { user } = asPlatformAdmin('/platform/users')
    expect(await screen.findByRole('link', { name: 'Acme' })).toHaveAttribute('href', '/w/acme')
    expect(screen.getByText('Old')).toHaveClass('line-through')
    expect(screen.getByText('Platform Admin')).toBeInTheDocument()
    // No way to disable yourself.
    const me = screen.getByRole('cell', { name: /owner@example.test/ }).closest('tr') as HTMLElement
    expect(within(me).queryByRole('button')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Disabled' }))
    await user.type(screen.getByLabelText('Search by email'), 'ana')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => {
      expect(queries.at(-1)?.get('email')).toBe('ana')
    })
    expect(queries.at(-1)?.get('is_active')).toBe('false')

    const ana = screen.getByText('ana@example.com').closest('tr') as HTMLElement
    await user.click(within(ana).getByRole('button', { name: 'Disable' }))
    await user.click(await screen.findByRole('button', { name: 'Disable' }))
    expect(await screen.findByText('ana@example.com disabled')).toBeInTheDocument()
    expect(patched).toEqual({ is_active: false })

    const bo = screen.getByText('bo@example.com').closest('tr') as HTMLElement
    await user.click(within(bo).getByRole('button', { name: 'Enable' }))
    expect(await screen.findByText(/sign in again with their roles/)).toBeInTheDocument()
  })
})

describe('platform developers', () => {
  it('blocks and unblocks developers', async () => {
    const calls: unknown[] = []
    server.use(
      http.get(api('/api/v1/developers'), () =>
        json(
          page([
            { id: 'd-1', email: 'dev@example.com', status: 'active', created_at: STAMP },
            { id: 'd-2', email: 'spam@example.com', status: 'blocked', created_at: STAMP },
          ]),
        ),
      ),
      http.patch(api('/api/v1/developers/:id'), async ({ request, params }) => {
        calls.push([params.id, await request.json()])
        return json({ id: 'd-1', email: 'dev@example.com', status: 'blocked', created_at: STAMP })
      }),
    )
    const { user } = asPlatformAdmin('/platform/developers')
    await user.click(await screen.findByRole('button', { name: 'Block' }))
    expect(await screen.findByText(/blocked in every workspace/)).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Block' }).at(-1) as HTMLElement)
    await screen.findByText('dev@example.com blocked')
    await user.click(screen.getByRole('button', { name: 'Unblock' }))
    await user.click(await screen.findByRole('button', { name: 'Unblock' }))
    await screen.findByText('spam@example.com unblocked')
    expect(calls).toEqual([
      ['d-1', { status: 'blocked' }],
      ['d-2', { status: 'active' }],
    ])
  })
})

describe('platform activity', () => {
  it('shows every event with its workspace and filters by workspace', async () => {
    const asked: (string | null)[] = []
    server.use(
      http.get(api('/api/v1/workspaces'), () => json(page([workspace()]))),
      http.get(api('/api/v1/audit-events'), ({ request }) => {
        asked.push(new URL(request.url).searchParams.get('workspace_id'))
        return json(
          page([
            makeEvent(),
            makeEvent({
              id: 'e-2',
              action: 'workspace.suspended',
              workspace_id: null,
              actor_type: 'platform_admin',
            }),
          ]),
        )
      }),
    )
    const { user } = asPlatformAdmin('/platform/activity')
    expect(await screen.findByText('workspace.suspended')).toBeInTheDocument()
    expect(await screen.findByText('acme')).toBeInTheDocument()
    expect(screen.getByText('Platform', { selector: 'td span' })).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Workspace' }))
    await user.click(await screen.findByRole('option', { name: 'Acme (acme)' }))
    await waitFor(() => {
      expect(asked).toContain('w-1')
    })
  })
})
