import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { AuthCard } from '@/components/auth-card'
import { FormError } from '@/components/form-error'
import { FullPageLoading } from '@/components/states'
import { Button } from '@/components/ui/button'
import { useMemberAuth } from '@/lib/auth/member'
import { safeNextPath } from '@/lib/format'
import { ForgotPasswordForm, MfaCodeForm, NewPasswordForm, PasswordSignInForm } from './forms'

export function LoginPage() {
  const auth = useMemberAuth()
  const [params] = useSearchParams()
  const next = safeNextPath(params.get('next'))

  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status === 'signed_in') {
    return <Navigate to={auth.passwordSetupPending ? '/set-password' : next} replace />
  }

  if (auth.status === 'mfa_required') {
    return (
      <AuthCard
        title="Two-step verification"
        description={`Signed in as ${auth.session?.user.email ?? ''}.`}
        footer={
          <Button variant="link" onClick={() => void auth.signOut()}>
            Use another account
          </Button>
        }
      >
        <MfaCodeForm auth={auth} />
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Sign in"
      description="Workspace members and the Platform Admin. Developers sign in on the portal."
      footer={
        <a href="/portal" className="hover:underline">
          Developer portal →
        </a>
      }
    >
      <div className="grid gap-4">
        <FormError message={auth.linkError && `That link did not work: ${auth.linkError}`} />
        <PasswordSignInForm auth={auth} forgotHref="/forgot-password" />
      </div>
    </AuthCard>
  )
}

export function ForgotPasswordPage() {
  const auth = useMemberAuth()
  return (
    <AuthCard
      title="Forgot your password?"
      description="We'll email you a link to choose a new one."
      footer={
        <a href="/login" className="hover:underline">
          ← Back to sign in
        </a>
      }
    >
      <ForgotPasswordForm auth={auth} redirectTo={`${window.location.origin}/`} />
    </AuthCard>
  )
}

/**
 * After an invitation or a recovery link: the person is signed in by the
 * link and chooses a password. With MFA set up, GoTrue wants the code first.
 */
export function SetPasswordPage() {
  const auth = useMemberAuth()
  const navigate = useNavigate()

  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status === 'signed_out') return <Navigate to="/login" replace />
  if (auth.status === 'mfa_required') {
    return (
      <AuthCard title="Verify it's you" description="Enter a code before choosing a new password.">
        <MfaCodeForm auth={auth} />
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Choose your password"
      description={`For ${auth.session?.user.email ?? 'your account'}.`}
    >
      <NewPasswordForm
        auth={auth}
        submitLabel="Save and continue"
        onDone={() => {
          auth.finishPasswordSetup()
          void navigate('/', { replace: true })
        }}
      />
    </AuthCard>
  )
}
