/**
 * GoTrue's email links (invitation, password recovery, sign-up
 * confirmation) end on the app with the session or an error in the URL
 * fragment: `#access_token=...&refresh_token=...&type=invite`.
 */
export type AuthRedirect =
  | { kind: 'session'; accessToken: string; refreshToken: string; type: string }
  | { kind: 'error'; message: string }

export function parseAuthRedirect(hash: string): AuthRedirect | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const error = params.get('error_description') ?? params.get('error')
  if (error) return { kind: 'error', message: error.replace(/\+/g, ' ') }
  const accessToken = params.get('access_token')
  const refreshToken = params.get('refresh_token')
  if (!accessToken || !refreshToken) return null
  return { kind: 'session', accessToken, refreshToken, type: params.get('type') ?? '' }
}

/**
 * Reads the redirect from the current URL and removes it from the address
 * bar (and history), so the tokens are not left in the URL.
 */
export function takeAuthRedirect(): AuthRedirect | null {
  const redirect = parseAuthRedirect(window.location.hash)
  if (redirect) {
    const { pathname, search } = window.location
    window.history.replaceState(window.history.state, '', pathname + search)
  }
  return redirect
}

/** Link types after which the person must choose a (new) password. */
export function needsNewPassword(type: string): boolean {
  return type === 'invite' || type === 'recovery'
}
