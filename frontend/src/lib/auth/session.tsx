import type { GoTrueClient, Session } from '@supabase/auth-js'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { needsNewPassword, takeAuthRedirect } from './redirect'

/**
 * - `mfa_required`: the password was right and the person has a verified
 *   authenticator, but this session hasn't been stepped up to `aal2` yet.
 */
export type AuthStatus = 'loading' | 'signed_out' | 'mfa_required' | 'signed_in'

export interface AuthState {
  status: AuthStatus
  session: Session | null
  /** `aal2` once the session was verified with an authenticator code. */
  aal: 'aal1' | 'aal2' | null
  /** The person has at least one verified authenticator. */
  mfaEnrolled: boolean
  /** Opened an invitation or recovery link: a password must be chosen. */
  passwordSetupPending: boolean
  /** An email link that failed (expired, used), to show on the login page. */
  linkError: string | null
  client: GoTrueClient
  signIn: (email: string, password: string) => Promise<void>
  /** The person's verified authenticator apps, to pick one when there are several. */
  listAuthenticators: () => Promise<Authenticator[]>
  verifyMfa: (code: string, factorId: string) => Promise<void>
  signOut: (scope?: 'local' | 'global') => Promise<void>
  /** Reads the session again, after a change the client made itself (MFA setup). */
  refresh: () => Promise<void>
  finishPasswordSetup: () => void
}

interface Snapshot {
  status: AuthStatus
  session: Session | null
  aal: 'aal1' | 'aal2' | null
  mfaEnrolled: boolean
}

const PASSWORD_SETUP_KEY = 'password-setup'

async function readSnapshot(client: GoTrueClient): Promise<Snapshot> {
  const { data } = await client.getSession()
  const session = data.session
  if (!session) return { status: 'signed_out', session: null, aal: null, mfaEnrolled: false }
  const { data: level } = await client.mfa.getAuthenticatorAssuranceLevel()
  const aal = level?.currentLevel === 'aal2' ? 'aal2' : 'aal1'
  const mfaEnrolled = level?.nextLevel === 'aal2'
  const status = mfaEnrolled && aal !== 'aal2' ? 'mfa_required' : 'signed_in'
  return { status, session, aal, mfaEnrolled }
}

export interface Authenticator {
  id: string
  name: string
}

async function verifiedAuthenticators(client: GoTrueClient): Promise<Authenticator[]> {
  const { data, error } = await client.mfa.listFactors()
  if (error) throw error
  return data.totp.map((f) => ({ id: f.id, name: f.friendly_name ?? 'Authenticator app' }))
}

function readFlag(key: string): boolean {
  try {
    return sessionStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, on: boolean): void {
  try {
    if (on) sessionStorage.setItem(key, '1')
    else sessionStorage.removeItem(key)
  } catch {
    // Without storage the flag lasts until the page is reloaded.
  }
}

/**
 * Makes a provider and hook for one GoTrue server. The app has two: one for
 * members, one for developers (src/lib/auth/gotrue.ts).
 */
export function createAuth(client: GoTrueClient, name: string) {
  const Context = createContext<AuthState | null>(null)
  const flagKey = `${name}-${PASSWORD_SETUP_KEY}`

  function AuthProvider({ children }: { children: React.ReactNode }) {
    const [snapshot, setSnapshot] = useState<Snapshot>({
      status: 'loading',
      session: null,
      aal: null,
      mfaEnrolled: false,
    })
    const [passwordSetupPending, setPasswordSetupPending] = useState(() => readFlag(flagKey))
    const [linkError, setLinkError] = useState<string | null>(null)

    const refresh = useCallback(async () => {
      setSnapshot(await readSnapshot(client))
    }, [])

    useEffect(() => {
      let active = true
      async function start() {
        const redirect = takeAuthRedirect()
        if (redirect?.kind === 'error') setLinkError(redirect.message)
        if (redirect?.kind === 'session') {
          const { error } = await client.setSession({
            access_token: redirect.accessToken,
            refresh_token: redirect.refreshToken,
          })
          if (error) setLinkError(error.message)
          else if (needsNewPassword(redirect.type)) {
            writeFlag(flagKey, true)
            setPasswordSetupPending(true)
          }
        }
        const next = await readSnapshot(client)
        if (active) setSnapshot(next)
      }
      void start()
      // GoTrueClient holds a lock while it calls back: read the session after it.
      const { data } = client.onAuthStateChange(() => {
        setTimeout(() => {
          if (!active) return
          void readSnapshot(client).then((next) => {
            if (active) setSnapshot(next)
          })
        }, 0)
      })
      return () => {
        active = false
        data.subscription.unsubscribe()
      }
    }, [])

    const signIn = useCallback(async (email: string, password: string) => {
      const { error } = await client.signInWithPassword({ email, password })
      if (error) throw error
      setLinkError(null)
      setSnapshot(await readSnapshot(client))
    }, [])

    const listAuthenticators = useCallback(() => verifiedAuthenticators(client), [])

    const verifyMfa = useCallback(async (code: string, factorId: string) => {
      const { error } = await client.mfa.challengeAndVerify({ factorId, code })
      if (error) throw error
      setSnapshot(await readSnapshot(client))
    }, [])

    const signOut = useCallback(async (scope: 'local' | 'global' = 'local') => {
      const { error } = await client.signOut({ scope })
      // A global sign-out that fails still ends this browser's session.
      if (error) await client.signOut({ scope: 'local' })
      writeFlag(flagKey, false)
      setPasswordSetupPending(false)
      setSnapshot({ status: 'signed_out', session: null, aal: null, mfaEnrolled: false })
    }, [])

    const finishPasswordSetup = useCallback(() => {
      writeFlag(flagKey, false)
      setPasswordSetupPending(false)
    }, [])

    const value = useMemo<AuthState>(
      () => ({
        ...snapshot,
        passwordSetupPending,
        linkError,
        client,
        signIn,
        listAuthenticators,
        verifyMfa,
        signOut,
        refresh,
        finishPasswordSetup,
      }),
      [
        snapshot,
        passwordSetupPending,
        linkError,
        signIn,
        listAuthenticators,
        verifyMfa,
        signOut,
        refresh,
        finishPasswordSetup,
      ],
    )
    return <Context.Provider value={value}>{children}</Context.Provider>
  }

  function useAuth(): AuthState {
    const state = useContext(Context)
    if (!state) throw new Error(`use${name}Auth outside its provider`)
    return state
  }

  return { AuthProvider, useAuth }
}
