import { zodResolver } from '@hookform/resolvers/zod'
import { MailCheck } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'
import { z } from 'zod'
import { AuthCard } from '@/components/auth-card'
import { TextField } from '@/components/fields'
import { FormError } from '@/components/form-error'
import { FullPageLoading, Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Form } from '@/components/ui/form'
import {
  ForgotPasswordForm,
  NewPasswordForm,
  PASSWORD_MIN_LENGTH,
  PasswordSignInForm,
} from '@/features/auth/forms'
import { useDeveloperAuth } from '@/lib/auth/developer'
import { authErrorMessage } from '@/lib/auth/errors'
import { safeNextPath } from '@/lib/format'
import { portalLinkTarget } from './gateway'

const TITLE = 'API Platform · Developers'

/** A path inside the portal to go to after sign-in; anything else goes to the catalog. */
function portalNext(value: string | null): string {
  const next = safeNextPath(value)
  return next.startsWith('/portal') ? next : '/portal'
}

export function PortalLoginPage() {
  const auth = useDeveloperAuth()
  const [params] = useSearchParams()

  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status === 'signed_in') {
    return (
      <Navigate
        to={auth.passwordSetupPending ? '/portal/set-password' : portalNext(params.get('next'))}
        replace
      />
    )
  }
  return (
    <AuthCard
      title="Sign in to the developer portal"
      description="Browse the APIs, create apps and get your keys."
      footer={
        <span className="flex flex-col items-center gap-1">
          <span>
            No account?{' '}
            <Link to="/portal/signup" className="text-foreground hover:underline">
              Sign up
            </Link>
          </span>
          <a href="/login" className="hover:underline">
            Workspace members sign in here →
          </a>
        </span>
      }
    >
      <div className="grid gap-4">
        <FormError message={auth.linkError && `That link did not work: ${auth.linkError}`} />
        <PasswordSignInForm auth={auth} forgotHref="/portal/forgot-password" />
      </div>
    </AuthCard>
  )
}

const signUpSchema = z
  .object({
    email: z.string().trim().email('Enter a valid email'),
    password: z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters`),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ['confirm'],
    message: 'The passwords do not match',
  })
type SignUpValues = z.infer<typeof signUpSchema>

/** Open sign-up: GoTrue emails a confirmation link; the account works once it is opened. */
export function PortalSignUpPage() {
  const auth = useDeveloperAuth()
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const form = useForm<SignUpValues>({
    resolver: zodResolver(signUpSchema),
    defaultValues: { email: '', password: '', confirm: '' },
  })

  const submit = form.handleSubmit(async ({ email, password }) => {
    setError(null)
    const { error: failure } = await auth.client.signUp({
      email,
      password,
      options: { emailRedirectTo: portalLinkTarget() },
    })
    if (failure) setError(authErrorMessage(failure))
    else setSentTo(email)
  })

  if (auth.status === 'signed_in') return <Navigate to="/portal" replace />

  if (sentTo) {
    return (
      <AuthCard title="Check your email" footer={<Link to="/portal/login">← Back to sign in</Link>}>
        <div role="status" className="flex flex-col items-center gap-3 text-center text-sm">
          <MailCheck aria-hidden className="text-success size-10" />
          <p>
            We sent a confirmation link to <strong>{sentTo}</strong>. Open it to finish signing up;
            it brings you back here, signed in.
          </p>
        </div>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Create a developer account"
      description={TITLE}
      footer={
        <span>
          Have an account?{' '}
          <Link to="/portal/login" className="text-foreground hover:underline">
            Sign in
          </Link>
        </span>
      }
    >
      <Form {...form}>
        <form onSubmit={(e) => void submit(e)} className="grid gap-4" noValidate>
          <TextField
            control={form.control}
            name="email"
            label="Email"
            type="email"
            autoComplete="email"
          />
          <TextField
            control={form.control}
            name="password"
            label="Password"
            type="password"
            autoComplete="new-password"
            description={`At least ${PASSWORD_MIN_LENGTH} characters.`}
          />
          <TextField
            control={form.control}
            name="confirm"
            label="Repeat the password"
            type="password"
            autoComplete="new-password"
          />
          <FormError message={error} />
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting && <Spinner />}
            Sign up
          </Button>
        </form>
      </Form>
    </AuthCard>
  )
}

export function PortalForgotPasswordPage() {
  const auth = useDeveloperAuth()
  return (
    <AuthCard
      title="Forgot your password?"
      description="We'll email you a link to choose a new one."
      footer={<Link to="/portal/login">← Back to sign in</Link>}
    >
      <ForgotPasswordForm auth={auth} redirectTo={portalLinkTarget()} />
    </AuthCard>
  )
}

/** After a recovery link: choose a new password. */
export function PortalSetPasswordPage() {
  const auth = useDeveloperAuth()
  const navigate = useNavigate()
  if (auth.status === 'loading') return <FullPageLoading />
  if (auth.status !== 'signed_in') return <Navigate to="/portal/login" replace />
  return (
    <AuthCard title="Choose a new password" description={auth.session?.user.email}>
      <NewPasswordForm
        auth={auth}
        submitLabel="Save and continue"
        onDone={() => {
          auth.finishPasswordSetup()
          void navigate('/portal', { replace: true })
        }}
      />
    </AuthCard>
  )
}
