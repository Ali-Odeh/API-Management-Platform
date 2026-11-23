/** The methods a route can serve (and CORS can allow). */
export const HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'] as const

/** A status code's color: 2xx/3xx success, 4xx warning, 5xx error. */
export function statusClass(status: number): string {
  if (status >= 500) return 'text-destructive'
  if (status >= 400) return 'text-warning'
  return 'text-success'
}
