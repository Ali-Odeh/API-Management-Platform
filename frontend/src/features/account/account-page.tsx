import { LogOut } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { NewPasswordForm } from '@/features/auth/forms'
import { useMe } from '@/features/me/queries'
import { useMemberAuth } from '@/lib/auth/member'
import { formatDate } from '@/lib/format'
import { MfaSection } from './mfa-section'

/** `/account`: email, two-step verification, password, sessions. */
export function AccountPage() {
  const auth = useMemberAuth()
  const { data: me } = useMe()
  const [signingOutEverywhere, setSigningOutEverywhere] = useState(false)

  return (
    <>
      <PageHeader title="Account and security" />
      <div className="grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-1 text-sm">
            <p className="flex items-center gap-2">
              <span className="font-medium">{auth.session?.user.email}</span>
              {me?.is_platform_admin && <Badge>Platform Admin</Badge>}
            </p>
            {me && (
              <p className="text-muted-foreground">Member since {formatDate(me.created_at)}</p>
            )}
          </CardContent>
        </Card>

        <MfaSection auth={auth} />

        <Card>
          <CardHeader>
            <CardTitle>Password</CardTitle>
            <CardDescription>Choose a new password for signing in.</CardDescription>
          </CardHeader>
          <CardContent className="max-w-sm">
            <NewPasswordForm
              auth={auth}
              submitLabel="Change password"
              onDone={() => toast.success('Password changed')}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Sessions</CardTitle>
            <CardDescription>
              Signs you out on every browser and device, this one included.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              onClick={() => {
                setSigningOutEverywhere(true)
              }}
            >
              <LogOut /> Sign out of all devices
            </Button>
          </CardContent>
        </Card>
      </div>
      <ConfirmDialog
        open={signingOutEverywhere}
        onOpenChange={setSigningOutEverywhere}
        title="Sign out everywhere?"
        description="Every session of your account ends, including this one."
        confirmLabel="Sign out everywhere"
        onConfirm={() => auth.signOut('global')}
      />
    </>
  )
}
