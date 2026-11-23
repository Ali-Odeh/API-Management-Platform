import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VALID_CODE } from '@/test/gotrue'
import { renderSignedIn } from '@/test/render'
import { makeUser, memberGoTrue, state } from '@/test/server'

describe('account page', () => {
  it('shows the profile', async () => {
    state.me = makeUser({ is_platform_admin: true })
    renderSignedIn('/account')
    expect(await screen.findByText('Platform Admin')).toBeInTheDocument()
    expect(screen.getAllByText('owner@example.test').length).toBeGreaterThan(0)
    expect(await screen.findByText('Member since Nov 20, 2026')).toBeInTheDocument()
  })

  it('sets up an authenticator app with a QR code and a code', async () => {
    const { user } = renderSignedIn('/account')
    expect(await screen.findByText('No authenticator app is set up.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Set up an authenticator app/ }))
    await user.clear(screen.getByLabelText('Name of this authenticator'))
    await user.type(screen.getByLabelText('Name of this authenticator'), 'Phone')
    await user.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByAltText('QR code for your authenticator app')).toBeInTheDocument()
    expect(screen.getByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument()
    const turnOn = screen.getByRole('button', { name: 'Turn on' })
    expect(turnOn).toBeDisabled()

    await user.type(screen.getByLabelText(/6-digit code/), '000000')
    await user.click(turnOn)
    expect(await screen.findByRole('alert')).toHaveTextContent('That code is not right')

    await user.clear(screen.getByLabelText(/6-digit code/))
    await user.type(screen.getByLabelText(/6-digit code/), VALID_CODE)
    await user.click(screen.getByRole('button', { name: 'Turn on' }))

    expect(await screen.findByText('verified with a code')).toBeInTheDocument()
    expect(screen.getByText('Phone')).toBeInTheDocument()
  })

  it('cancels a setup, removing the unfinished factor', async () => {
    const { user } = renderSignedIn('/account')
    await user.click(await screen.findByRole('button', { name: /Set up an authenticator app/ }))
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByAltText('QR code for your authenticator app')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('No authenticator app is set up.')).toBeInTheDocument()
    const [person] = memberGoTrue.users.values()
    expect(person?.factors).toEqual([])
  })

  it('refuses a second authenticator with the same name', async () => {
    const { user } = renderSignedIn('/account', { mfa: true, aal: 'aal2' })
    await user.click(await screen.findByRole('button', { name: /Set up an authenticator app/ }))
    await user.clear(screen.getByLabelText('Name of this authenticator'))
    await user.type(screen.getByLabelText('Name of this authenticator'), 'Phone')
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You already have an authenticator with this name.',
    )
  })

  it('removes an authenticator, which needs a verified session', async () => {
    const { user } = renderSignedIn('/account', { mfa: true, aal: 'aal2' })
    const item = (await screen.findByText('Phone')).closest('li') as HTMLElement
    await user.click(within(item).getByRole('button', { name: 'Remove Phone' }))
    await user.click(await screen.findByRole('button', { name: 'Remove' }))
    expect(await screen.findByText('No authenticator app is set up.')).toBeInTheDocument()
  })

  it('changes the password', async () => {
    const { user } = renderSignedIn('/account')
    await user.type(await screen.findByLabelText('New password'), 'another-long-password')
    await user.type(screen.getByLabelText('Repeat the password'), 'another-long-password')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password changed')).toBeInTheDocument()
  })

  it('reports GoTrue refusing the same password', async () => {
    const { user } = renderSignedIn('/account')
    await user.type(await screen.findByLabelText('New password'), 'correct-horse-battery')
    await user.type(screen.getByLabelText('Repeat the password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('must differ')
  })

  it('signs out of all devices after confirming', async () => {
    const { user } = renderSignedIn('/account')
    await user.click(await screen.findByRole('button', { name: /Sign out of all devices/ }))
    await user.click(await screen.findByRole('button', { name: 'Sign out everywhere' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(memberGoTrue.calls).toContain('logout:global')
  })
})
