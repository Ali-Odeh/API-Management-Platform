import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VALID_CODE } from '@/test/gotrue'
import { renderApp, renderSignedIn } from '@/test/render'
import { MEMBER_STORAGE_KEY, memberGoTrue } from '@/test/server'

const PASSWORD = 'correct-horse-battery'

async function signIn(
  user: ReturnType<typeof renderApp>['user'],
  email: string,
  password = PASSWORD,
) {
  await user.type(await screen.findByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('login', () => {
  it('sends a signed-out visitor to the login page, then back where they wanted to go', async () => {
    memberGoTrue.addUser('owner@example.test', PASSWORD)
    const { user, router } = renderApp('/account')
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('?next=%2Faccount')

    await signIn(user, 'owner@example.test')

    expect(await screen.findByRole('heading', { name: 'Account and security' })).toBeInTheDocument()
  })

  it('shows a wrong password and a disabled account in words', async () => {
    memberGoTrue.addUser('banned@example.test', PASSWORD, { banned: true })
    const { user } = renderApp('/login')

    await signIn(user, 'nobody@example.test')
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong email or password.')

    await user.clear(screen.getByLabelText('Email'))
    await user.clear(screen.getByLabelText('Password'))
    await signIn(user, 'banned@example.test')
    expect(await screen.findByRole('alert')).toHaveTextContent('This account is disabled.')
  })

  it('validates the fields before calling GoTrue', async () => {
    const { user } = renderApp('/login')
    await user.click(await screen.findByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter a valid email')).toBeInTheDocument()
    expect(screen.getByText('Enter your password')).toBeInTheDocument()
    expect(memberGoTrue.calls).toEqual([])
  })

  it('asks for the authenticator code when the person has MFA', async () => {
    memberGoTrue.addUser('admin@example.test', PASSWORD, { mfa: true })
    const { user } = renderApp('/login')
    await signIn(user, 'admin@example.test')

    const code = await screen.findByLabelText('Authentication code')
    await user.type(code, '000000')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That code is not right')

    await user.type(screen.getByLabelText('Authentication code'), VALID_CODE)
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByRole('heading', { name: 'My workspaces' })).toBeInTheDocument()
  })

  it('rejects a code that is not 6 digits without calling GoTrue', async () => {
    memberGoTrue.addUser('admin@example.test', PASSWORD, { mfa: true })
    memberGoTrue.storeSession(MEMBER_STORAGE_KEY, 'admin@example.test', 'aal1')
    const { user } = renderApp('/login')
    await user.type(await screen.findByLabelText('Authentication code'), '12ab')
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText('Enter the 6-digit code from your app')).toBeInTheDocument()
    expect(memberGoTrue.calls).not.toContain('factor:verify')
  })

  it('lets a person stopped at the MFA step use another account', async () => {
    memberGoTrue.addUser('admin@example.test', PASSWORD, { mfa: true })
    memberGoTrue.storeSession(MEMBER_STORAGE_KEY, 'admin@example.test', 'aal1')
    const { user } = renderApp('/')
    await user.click(await screen.findByRole('button', { name: 'Use another account' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('never redirects after login to another site', async () => {
    memberGoTrue.addUser('owner@example.test', PASSWORD)
    const { user, router } = renderApp('/login?next=//evil.example')
    await signIn(user, 'owner@example.test')
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/')
    })
  })

  it('signs out from the account menu', async () => {
    const { user } = renderSignedIn('/')
    await user.click(await screen.findByRole('button', { name: 'Account menu' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(memberGoTrue.calls).toContain('logout:local')
  })
})

describe('email links', () => {
  function openLink(fragment: string) {
    window.history.replaceState(null, '', `/${fragment}`)
  }

  it('makes an invited person choose a password first', async () => {
    const person = memberGoTrue.addUser('new@example.test', 'unknown-password')
    memberGoTrue.storeSession('scratch', 'new@example.test')
    const stored = JSON.parse(localStorage.getItem('scratch') ?? '{}') as {
      access_token: string
      refresh_token: string
    }
    localStorage.removeItem('scratch')
    openLink(
      `#access_token=${stored.access_token}&refresh_token=${stored.refresh_token}&type=invite`,
    )

    const { user } = renderApp('/')
    expect(await screen.findByRole('heading', { name: 'Choose your password' })).toBeInTheDocument()
    expect(window.location.hash).toBe('')

    await user.type(screen.getByLabelText('New password'), 'short')
    await user.type(screen.getByLabelText('Repeat the password'), 'other')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByText('Use at least 12 characters')).toBeInTheDocument()
    expect(screen.getByText('The passwords do not match')).toBeInTheDocument()

    await user.clear(screen.getByLabelText('New password'))
    await user.clear(screen.getByLabelText('Repeat the password'))
    await user.type(screen.getByLabelText('New password'), 'a-brand-new-password')
    await user.type(screen.getByLabelText('Repeat the password'), 'a-brand-new-password')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))

    expect(await screen.findByRole('heading', { name: 'My workspaces' })).toBeInTheDocument()
    expect(person.password).toBe('a-brand-new-password')
  })

  it('shows why a link failed on the login page', async () => {
    openLink('#error=access_denied&error_description=Email+link+is+invalid+or+has+expired')
    renderApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That link did not work: Email link is invalid or has expired',
    )
  })

  it('sends a recovery email', async () => {
    const { user } = renderApp('/forgot-password')
    await user.type(await screen.findByLabelText('Email'), 'owner@example.test')
    await user.click(screen.getByRole('button', { name: 'Send the link' }))
    expect(await screen.findByRole('status')).toHaveTextContent('owner@example.test')
    expect(memberGoTrue.calls).toContain('recover:owner@example.test')
  })

  it('sends a signed-out visitor of the password page to the login page', async () => {
    renderApp('/set-password')
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })
})
