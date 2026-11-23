import { screen, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { asRole } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('members', () => {
  const members = [
    {
      user_id: 'u-1',
      email: 'owner@example.test',
      role: 'owner' as const,
      is_active: true,
      added_by: null,
      created_at: '2026-11-20T10:00:00Z',
    },
    {
      user_id: 'u-2',
      email: 'ana@example.test',
      role: 'viewer' as const,
      is_active: true,
      added_by: 'u-1',
      created_at: '2026-11-21T10:00:00Z',
    },
    {
      user_id: 'u-3',
      email: 'gone@example.test',
      role: 'api_admin' as const,
      is_active: false,
      added_by: 'u-1',
      created_at: '2026-11-21T10:00:00Z',
    },
  ]

  it('lists members, warns about a single owner, changes a role and removes one', async () => {
    const calls: string[] = []
    server.use(
      http.get(api(`${W}/members`), () => json(page(members))),
      http.patch(api(`${W}/members/:id`), async ({ request, params }) => {
        calls.push(`role ${String(params.id)} ${JSON.stringify(await request.json())}`)
        return json(members[1] ?? {})
      }),
      http.delete(api(`${W}/members/:id`), ({ params }) => {
        calls.push(`remove ${String(params.id)}`)
        return new Response(null, { status: 204 })
      }),
    )
    const { user } = renderSignedIn('/w/acme/members')
    expect(await screen.findByText(/is the only owner/)).toBeInTheDocument()
    expect(screen.getByText('disabled')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Role of owner@example.test' })).toBeDisabled()

    await user.click(screen.getByRole('combobox', { name: 'Role of ana@example.test' }))
    await user.click(await screen.findByRole('option', { name: 'API Admin' }))
    await screen.findByText('ana@example.test is now API Admin')

    await user.click(screen.getByRole('button', { name: 'Remove ana@example.test' }))
    await user.click(await screen.findByRole('button', { name: 'Remove member' }))
    await screen.findByText('ana@example.test removed')
    expect(calls).toEqual(['role u-2 {"role":"api_admin"}', 'remove u-2'])
  })

  it('adds a member and says when an invitation went out', async () => {
    let body: unknown
    server.use(
      http.get(api(`${W}/members`), () => json(page(members))),
      http.post(api(`${W}/members`), async ({ request }) => {
        body = await request.json()
        return json({ ...members[1], email: 'new@example.test', invited: true }, 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/members')
    await user.click(await screen.findByRole('button', { name: /Add member/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Email'), 'new@example.test')
    await user.click(within(dialog).getByRole('combobox', { name: 'Role' }))
    await user.click(await screen.findByRole('option', { name: 'Owner' }))
    expect(within(dialog).getByText(/including members and deleting/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Add member' }))
    expect(await screen.findByText('Invitation sent to new@example.test')).toBeInTheDocument()
    expect(body).toEqual({ email: 'new@example.test', role: 'owner' })
  })

  it('shows a refused role change and gives a viewer badges only', async () => {
    server.use(
      http.get(api(`${W}/members`), () => json(page(members))),
      http.patch(api(`${W}/members/:id`), () => detail(409, 'last_owner')),
    )
    const { user, unmount } = renderSignedIn('/w/acme/members')
    await user.click(await screen.findByRole('combobox', { name: 'Role of ana@example.test' }))
    await user.click(await screen.findByRole('option', { name: 'Owner' }))
    expect(await screen.findByText('A workspace must keep at least one owner.')).toBeInTheDocument()
    unmount()

    asRole('viewer')
    renderSignedIn('/w/acme/members', { email: 'viewer@example.test' })
    await screen.findByText('ana@example.test')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add member/ })).not.toBeInTheDocument()
  })
})
