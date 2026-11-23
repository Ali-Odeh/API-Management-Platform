import { isAuthError } from '@supabase/auth-js'

const AUTH_CODE_TEXT: Record<string, string> = {
  invalid_credentials: 'Wrong email or password.',
  user_banned: 'This account is disabled.',
  email_not_confirmed: 'Confirm your email first: open the link we sent you.',
  mfa_verification_failed: 'That code is not right. Try the current one from your app.',
  mfa_challenge_expired: 'The code expired. Enter the current one.',
  over_request_rate_limit: 'Too many attempts. Wait a few minutes and try again.',
  over_email_send_rate_limit: 'Too many emails were sent. Wait a while and try again.',
  weak_password: 'Choose a stronger password (at least 12 characters).',
  same_password: 'The new password must differ from the current one.',
  insufficient_aal: 'Verify with your authenticator app first.',
  mfa_factor_name_conflict: 'You already have an authenticator with this name.',
  user_already_exists: 'An account with this email already exists.',
}

/** A sentence for a GoTrue error, by its code where it has one. */
export function authErrorMessage(error: unknown): string {
  if (isAuthError(error)) {
    const text = error.code ? AUTH_CODE_TEXT[error.code] : undefined
    if (text) return text
    if (error.status === 0 || error.name === 'AuthRetryableFetchError') {
      return 'The sign-in server could not be reached.'
    }
    return error.message
  }
  return error instanceof Error ? error.message : 'Something went wrong'
}
