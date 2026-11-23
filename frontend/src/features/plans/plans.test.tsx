import { screen, within } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { makePlan } from '@/test/fixtures'
import { renderSignedIn } from '@/test/render'
import { api, detail, json, page, server } from '@/test/server'

const W = '/api/v1/workspaces/acme'

describe('plans', () => {
  it('lists plans and creates one with a quota', async () => {
    let body: unknown
    server.use(
      http.get(api(`${W}/plans`), () =>
        json(
          page([
            makePlan({
              id: 'p-2',
              name: 'Gold',
              is_default: false,
              monthly_quota: 1000,
              portal_visible: true,
              requires_approval: true,
            }),
            makePlan(),
          ]),
        ),
      ),
      http.post(api(`${W}/plans`), async ({ request }) => {
        body = await request.json()
        return json(makePlan({ id: 'p-3', name: 'Free', is_default: false }), 201)
      }),
    )
    const { user } = renderSignedIn('/w/acme/plans')
    const cards = await screen.findAllByText(/requests \/ minute/)
    expect(cards).toHaveLength(2)
    expect(screen.getByText('Default', { selector: '[data-slot=badge]' })).toBeInTheDocument()
    expect(screen.getByText('On the portal')).toBeInTheDocument()
    expect(screen.getByText('1,000 requests a month')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete Default' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /New plan/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'Free')
    await user.clear(within(dialog).getByLabelText('Requests per minute'))
    await user.type(within(dialog).getByLabelText('Requests per minute'), '10')
    await user.type(within(dialog).getByLabelText('Requests per month'), '500')
    await user.click(
      within(dialog).getByRole('switch', { name: 'Offered on the developer portal' }),
    )
    await user.click(within(dialog).getByRole('button', { name: 'Create plan' }))
    expect(await screen.findByText('Plan Free created')).toBeInTheDocument()
    expect(body).toEqual({
      name: 'Free',
      rate_limit_per_min: 10,
      monthly_quota: 500,
      portal_visible: true,
      requires_approval: false,
    })
  })

  it('edits a plan, removing its quota, and deletes another', async () => {
    let patched: unknown
    server.use(
      http.get(api(`${W}/plans`), () =>
        json(
          page([
            makePlan(),
            makePlan({ id: 'p-2', name: 'Gold', is_default: false, monthly_quota: 1000 }),
          ]),
        ),
      ),
      http.patch(api(`${W}/plans/p-2`), async ({ request }) => {
        patched = await request.json()
        return json(makePlan({ id: 'p-2', name: 'Gold', is_default: false }))
      }),
      http.delete(api(`${W}/plans/p-2`), () => detail(409, 'Plan is in use by 3 consumers')),
    )
    const { user } = renderSignedIn('/w/acme/plans')
    await user.click(await screen.findByRole('button', { name: 'Edit Gold' }))
    await user.clear(await screen.findByLabelText('Requests per month'))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Plan Gold saved')
    expect(patched).toMatchObject({ monthly_quota: null })
    expect(patched).not.toHaveProperty('is_default')

    await user.click(screen.getByRole('button', { name: 'Delete Gold' }))
    await user.click(await screen.findByRole('button', { name: 'Delete plan' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Plan is in use by 3 consumers')
  })
})
