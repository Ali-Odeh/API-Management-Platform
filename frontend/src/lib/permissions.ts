import type { Role, WorkspaceStatus } from '@/api/types'

/** The control plane's permissions (PLAN.md section 6). */
export type Permission =
  | 'workspace:read'
  | 'workspace:write'
  | 'workspace:delete'
  | 'services:read'
  | 'services:write'
  | 'routes:read'
  | 'routes:write'
  | 'consumers:read'
  | 'consumers:write'
  | 'keys:read'
  | 'keys:write'
  | 'members:read'
  | 'members:write'
  | 'audit:read'
  | 'plans:read'
  | 'plans:write'
  | 'usage:read'
  | 'apis:read'
  | 'apis:write'
  | 'apps:read'
  | 'apps:write'

const VIEWER: readonly Permission[] = [
  'workspace:read',
  'services:read',
  'routes:read',
  'consumers:read',
  'keys:read',
  'members:read',
  'plans:read',
  'usage:read',
  'apis:read',
  'apps:read',
]

const API_ADMIN: readonly Permission[] = [
  ...VIEWER,
  'services:write',
  'routes:write',
  'consumers:write',
  'keys:write',
  'audit:read',
  'plans:write',
  'apis:write',
  'apps:write',
]

const OWNER: readonly Permission[] = [
  ...API_ADMIN,
  'members:write',
  'workspace:write',
  'workspace:delete',
]

/** The same map as the control plane's; the server still checks every call. */
const ROLE_PERMISSIONS: Record<Role, ReadonlySet<Permission>> = {
  viewer: new Set(VIEWER),
  api_admin: new Set(API_ADMIN),
  owner: new Set(OWNER),
}

export const ROLE_LABELS: Record<Role, string> = {
  owner: 'Owner',
  api_admin: 'API Admin',
  viewer: 'Viewer',
}

function isWrite(permission: Permission): boolean {
  return !permission.endsWith(':read')
}

/** What the person may do in one workspace. */
export interface Access {
  role: Role | null
  isPlatformAdmin: boolean
  /** `aal2`: signed in with an authenticator code too. */
  aal: 'aal1' | 'aal2' | null
  workspaceStatus: WorkspaceStatus
}

/**
 * - `allowed`: show the control.
 * - `needs_mfa`: only the Platform Admin's platform power allows it, and
 *   that needs a session verified with MFA: show it disabled, with a hint.
 * - `denied`: hide it.
 */
export type Decision = 'allowed' | 'needs_mfa' | 'denied'

/**
 * Mirrors the server's rules: a role allows what it holds; a suspended
 * workspace is read-only for members; the Platform Admin may do the rest
 * with MFA. Only for showing and hiding: the server decides every call.
 */
export function decide(access: Access, permission: Permission): Decision {
  const byRole = access.role !== null && ROLE_PERMISSIONS[access.role].has(permission)
  const frozen = access.workspaceStatus === 'suspended' && isWrite(permission)
  if (byRole && !frozen) return 'allowed'
  if (!access.isPlatformAdmin) return 'denied'
  return access.aal === 'aal2' ? 'allowed' : 'needs_mfa'
}
