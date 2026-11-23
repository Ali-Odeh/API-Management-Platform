import { useWorkspace } from '@/features/workspace/context'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { Decision, Permission } from '@/lib/permissions'

type ButtonProps = React.ComponentProps<typeof Button>

export const MFA_HINT = 'Sign in again with your authenticator code to do this as Platform Admin.'

/**
 * A button for an action that needs a permission: hidden when the role
 * can't do it; shown disabled with a hint when only the Platform Admin's
 * power allows it and the session lacks MFA.
 */
export function GatedButton({ decision, ...props }: ButtonProps & { decision: Decision }) {
  if (decision === 'denied') return null
  if (decision === 'needs_mfa') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          {/* A disabled button gets no pointer events; the span shows the hint. */}
          <span tabIndex={0}>
            <Button {...props} disabled aria-describedby="mfa-hint" />
          </span>
        </TooltipTrigger>
        <TooltipContent id="mfa-hint">{MFA_HINT}</TooltipContent>
      </Tooltip>
    )
  }
  return <Button {...props} />
}

/** GatedButton for a permission in the current workspace. */
export function PermissionButton({
  permission,
  ...props
}: ButtonProps & { permission: Permission }) {
  const { can } = useWorkspace()
  return <GatedButton decision={can(permission)} {...props} />
}

/** Renders its children only when the permission is allowed outright. */
export function Can({
  permission,
  children,
}: {
  permission: Permission
  children: React.ReactNode
}) {
  const { can } = useWorkspace()
  return can(permission) === 'allowed' ? children : null
}
