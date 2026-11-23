/**
 * An answer from the control plane that wasn't a success. Most carry
 * `{"detail": "..."}`; a 403 has a short code there (`mfa_required`,
 * `permission_denied`, ...) and a 422 from validation has a list of field
 * errors, kept in `fields` by field name.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly fields: Record<string, string>

  constructor(status: number, detail: string, fields: Record<string, string> = {}) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.fields = fields
  }

  /** Builds the error from a response body of any shape. */
  static fromBody(status: number, body: unknown): ApiError {
    const detail = isObject(body) ? body.detail : undefined
    if (typeof detail === 'string') return new ApiError(status, detail)
    if (Array.isArray(detail)) return fromValidation(status, detail)
    return new ApiError(status, defaultMessage(status))
  }
}

interface ValidationItem {
  loc?: unknown
  msg?: unknown
}

function fromValidation(status: number, items: unknown[]): ApiError {
  const fields: Record<string, string> = {}
  const messages: string[] = []
  for (const item of items as ValidationItem[]) {
    const message = typeof item.msg === 'string' ? cleanMessage(item.msg) : 'Invalid value'
    const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body') : []
    const field = loc.map(String).join('.')
    if (field && !(field in fields)) fields[field] = message
    messages.push(field ? `${field}: ${message}` : message)
  }
  return new ApiError(status, messages.join('; ') || defaultMessage(status), fields)
}

/** Pydantic prefixes custom errors with "Value error, "; people don't need it. */
function cleanMessage(message: string): string {
  return message.replace(/^Value error, /, '')
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function defaultMessage(status: number): string {
  if (status === 0) return 'The server could not be reached'
  if (status >= 500) return 'Something went wrong on the server. Try again.'
  return `Request failed (${status})`
}

/** Human text for an error of any kind, for toasts and alerts. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return describeCode(error.detail)
  if (error instanceof Error) return error.message
  return 'Something went wrong'
}

const CODE_TEXT: Record<string, string> = {
  permission_denied: 'Your role does not allow this.',
  mfa_required: 'This needs a sign-in verified with your authenticator app (MFA).',
  platform_admin_required: 'Only the Platform Admin can do this.',
  user_disabled: 'Your account is disabled.',
  account_conflict: 'Another account already uses this email.',
  workspace_suspended: 'This workspace is suspended: it is read-only.',
  quota_exceeded: 'The workspace has reached its limit for this.',
  last_owner: 'A workspace must keep at least one owner.',
  default_workspace: 'The default workspace cannot be changed this way.',
  restore_period_over: 'The restore period of this workspace is over.',
  developer_blocked: 'Your developer account is blocked.',
  app_pending: 'This app is waiting for approval.',
  app_rejected: 'This app was rejected.',
  api_deprecated: 'This API is deprecated and takes no new apps.',
}

/** Turns the API's short codes into sentences; other details pass through. */
export function describeCode(detail: string): string {
  if (detail in CODE_TEXT) return CODE_TEXT[detail] as string
  const status = /^(workspace|api)_(\w+)$/.exec(detail)
  if (status) return `The ${status[1] === 'api' ? 'API' : 'workspace'} is ${status[2]}.`
  return detail
}
