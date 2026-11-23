import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link } from 'react-router'
import { z } from 'zod'
import { FormError } from '@/components/form-error'
import { Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { authErrorMessage } from '@/lib/auth/errors'
import type { AuthState } from '@/lib/auth/session'

/** GoTrue's GOTRUE_PASSWORD_MIN_LENGTH in docker-compose.yml. */
export const PASSWORD_MIN_LENGTH = 12

const signInSchema = z.object({
  email: z.string().trim().email('Enter a valid email'),
  password: z.string().min(1, 'Enter your password'),
})

export function PasswordSignInForm({ auth, forgotHref }: { auth: AuthState; forgotHref: string }) {
  const [error, setError] = useState<string | null>(null)
  const form = useForm({
    resolver: zodResolver(signInSchema),
    defaultValues: { email: '', password: '' },
  })

  async function submit(values: z.infer<typeof signInSchema>) {
    setError(null)
    try {
      await auth.signIn(values.email, values.password)
    } catch (e) {
      setError(authErrorMessage(e))
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} className="grid gap-4" noValidate>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="username" autoFocus {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center justify-between">
                <FormLabel>Password</FormLabel>
                <Link to={forgotHref} className="text-muted-foreground text-sm hover:underline">
                  Forgot password?
                </Link>
              </div>
              <FormControl>
                <Input type="password" autoComplete="current-password" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormError message={error} />
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting && <Spinner />}
          Sign in
        </Button>
      </form>
    </Form>
  )
}

const codeSchema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^\d{6}$/, 'Enter the 6-digit code from your app'),
})

/** The second sign-in step: a code from the authenticator app. */
export function MfaCodeForm({
  auth,
  submitLabel = 'Verify',
}: {
  auth: AuthState
  submitLabel?: string
}) {
  const [error, setError] = useState<string | null>(null)
  const form = useForm({ resolver: zodResolver(codeSchema), defaultValues: { code: '' } })

  async function submit(values: z.infer<typeof codeSchema>) {
    setError(null)
    try {
      await auth.verifyMfa(values.code)
    } catch (e) {
      setError(authErrorMessage(e))
      form.reset({ code: '' })
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} className="grid gap-4" noValidate>
        <FormField
          control={form.control}
          name="code"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Authentication code</FormLabel>
              <FormControl>
                <Input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  autoFocus
                  className="font-mono tracking-widest"
                  {...field}
                />
              </FormControl>
              <FormDescription>Open your authenticator app and enter its code.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormError message={error} />
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting && <Spinner />}
          {submitLabel}
        </Button>
      </form>
    </Form>
  )
}

const newPasswordSchema = z
  .object({
    password: z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters`),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ['confirm'],
    message: 'The passwords do not match',
  })

/** Sets the signed-in person's password (after an invitation or recovery link, or on the account page). */
export function NewPasswordForm({
  auth,
  submitLabel,
  onDone,
}: {
  auth: AuthState
  submitLabel: string
  onDone: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const form = useForm({
    resolver: zodResolver(newPasswordSchema),
    defaultValues: { password: '', confirm: '' },
  })

  async function submit(values: z.infer<typeof newPasswordSchema>) {
    setError(null)
    const { error: failure } = await auth.client.updateUser({ password: values.password })
    if (failure) {
      setError(authErrorMessage(failure))
      return
    }
    form.reset()
    onDone()
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} className="grid gap-4" noValidate>
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>New password</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormDescription>At least {PASSWORD_MIN_LENGTH} characters.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="confirm"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Repeat the password</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormError message={error} />
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting && <Spinner />}
          {submitLabel}
        </Button>
      </form>
    </Form>
  )
}

const emailSchema = z.object({ email: z.string().trim().email('Enter a valid email') })

/** Asks GoTrue for a recovery email; its link lands on `redirectTo`. */
export function ForgotPasswordForm({ auth, redirectTo }: { auth: AuthState; redirectTo: string }) {
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const form = useForm({ resolver: zodResolver(emailSchema), defaultValues: { email: '' } })

  async function submit(values: z.infer<typeof emailSchema>) {
    setError(null)
    const { error: failure } = await auth.client.resetPasswordForEmail(values.email, { redirectTo })
    if (failure) setError(authErrorMessage(failure))
    else setSentTo(values.email)
  }

  if (sentTo) {
    return (
      <p role="status" className="text-sm">
        If an account exists for <strong>{sentTo}</strong>, an email with a link to choose a new
        password is on its way.
      </p>
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} className="grid gap-4" noValidate>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="email" autoFocus {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormError message={error} />
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting && <Spinner />}
          Send the link
        </Button>
      </form>
    </Form>
  )
}
