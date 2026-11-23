import type { Factor } from '@supabase/auth-js'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { StatusBadge } from '@/components/badges'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { FormError } from '@/components/form-error'
import { ErrorState, LoadingState, Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { authErrorMessage } from '@/lib/auth/errors'
import type { AuthState } from '@/lib/auth/session'
import { formatDate } from '@/lib/format'

const FACTORS_KEY = ['auth', 'factors']

/** Authenticator apps (TOTP): list, set up with a QR code, remove. */
export function MfaSection({ auth }: { auth: AuthState }) {
  const queryClient = useQueryClient()
  const factors = useQuery({
    queryKey: FACTORS_KEY,
    queryFn: async () => {
      const { data, error } = await auth.client.mfa.listFactors()
      if (error) throw new Error(authErrorMessage(error))
      return data.all.filter((f) => f.factor_type === 'totp')
    },
  })
  const [enrolling, setEnrolling] = useState(false)
  const [removing, setRemoving] = useState<Factor | null>(null)

  async function changed() {
    await queryClient.invalidateQueries({ queryKey: FACTORS_KEY })
    await auth.refresh()
  }

  const verified = factors.data?.filter((f) => f.status === 'verified') ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-5" /> Two-step verification
        </CardTitle>
        <CardDescription>
          After your password, sign-in asks for a code from an authenticator app (Google
          Authenticator, 1Password, ...). The Platform pages require it.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {factors.isPending ? (
          <LoadingState />
        ) : factors.error ? (
          <ErrorState error={factors.error} onRetry={() => void factors.refetch()} />
        ) : (
          <>
            <p className="text-sm">
              {verified.length > 0 ? (
                <>
                  This session is{' '}
                  {auth.aal === 'aal2' ? <strong>verified with a code</strong> : 'not verified'}.
                </>
              ) : (
                'No authenticator app is set up.'
              )}
            </p>
            {factors.data.length > 0 && (
              <ul className="divide-y rounded-md border">
                {factors.data.map((factor) => (
                  <li key={factor.id} className="flex items-center justify-between gap-2 p-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">
                        {factor.friendly_name ?? 'Authenticator app'}
                      </p>
                      <p className="text-muted-foreground text-xs">
                        Added {formatDate(factor.created_at)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={factor.status === 'verified' ? 'active' : 'pending'} />
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove ${factor.friendly_name ?? 'authenticator'}`}
                        onClick={() => {
                          setRemoving(factor)
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {enrolling ? (
              <EnrollTotp
                auth={auth}
                onDone={async () => {
                  setEnrolling(false)
                  await changed()
                }}
              />
            ) : (
              <div>
                <Button
                  variant="outline"
                  onClick={() => {
                    setEnrolling(true)
                  }}
                >
                  <Plus /> Set up an authenticator app
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null)
        }}
        title="Remove this authenticator?"
        description={
          removing?.status === 'verified'
            ? 'Sign-in will no longer ask for its codes. Removing a verified one needs a session verified with a code.'
            : 'This setup was not finished.'
        }
        confirmLabel="Remove"
        destructive
        onConfirm={async () => {
          if (!removing) return
          const { error } = await auth.client.mfa.unenroll({ factorId: removing.id })
          if (error) throw new Error(authErrorMessage(error))
          toast.success('Authenticator removed')
          await changed()
        }}
      />
    </Card>
  )
}

interface Enrollment {
  id: string
  qrCode: string
  secret: string
}

/** Step 1: scan the QR code. Step 2: enter a code, which verifies the factor. */
function EnrollTotp({ auth, onDone }: { auth: AuthState; onDone: () => Promise<void> }) {
  const [name, setName] = useState('Authenticator app')
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function start() {
    setPending(true)
    setError(null)
    const { data, error: failure } = await auth.client.mfa.enroll({
      factorType: 'totp',
      friendlyName: name.trim(),
    })
    setPending(false)
    if (failure) {
      setError(authErrorMessage(failure))
      return
    }
    setEnrollment({ id: data.id, qrCode: data.totp.qr_code, secret: data.totp.secret })
  }

  async function verify() {
    if (!enrollment) return
    setPending(true)
    setError(null)
    const { error: failure } = await auth.client.mfa.challengeAndVerify({
      factorId: enrollment.id,
      code: code.trim(),
    })
    setPending(false)
    if (failure) {
      setError(authErrorMessage(failure))
      return
    }
    toast.success('Two-step verification is on')
    await onDone()
  }

  async function cancel() {
    if (enrollment) await auth.client.mfa.unenroll({ factorId: enrollment.id })
    await onDone()
  }

  if (!enrollment) {
    return (
      <div className="grid max-w-sm gap-3 rounded-md border p-4">
        <Label htmlFor="factor-name">Name of this authenticator</Label>
        <Input
          id="factor-name"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
          }}
        />
        <FormError message={error} />
        <div className="flex gap-2">
          <Button onClick={() => void start()} disabled={pending || !name.trim()}>
            {pending && <Spinner />} Continue
          </Button>
          <Button variant="ghost" onClick={() => void onDone()}>
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="grid gap-4 rounded-md border p-4 sm:grid-cols-[auto_1fr]">
      <img
        src={enrollment.qrCode}
        alt="QR code for your authenticator app"
        className="size-44 rounded bg-white p-2"
      />
      <form
        className="grid content-start gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          void verify()
        }}
      >
        <p className="text-sm">
          Scan the code with your app, or enter this key by hand:{' '}
          <code className="bg-muted rounded px-1 font-mono text-xs break-all">
            {enrollment.secret}
          </code>
        </p>
        <Label htmlFor="enroll-code">Then enter the 6-digit code it shows</Label>
        <Input
          id="enroll-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          className="max-w-40 font-mono tracking-widest"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
        />
        <FormError message={error} />
        <div className="flex gap-2">
          <Button type="submit" disabled={pending || !/^\d{6}$/.test(code.trim())}>
            {pending && <Spinner />} Turn on
          </Button>
          <Button type="button" variant="ghost" onClick={() => void cancel()}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  )
}
