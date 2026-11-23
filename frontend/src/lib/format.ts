import { format, formatDistanceToNowStrict } from 'date-fns'

/** `Nov 23, 2026, 18:04` in the viewer's time zone. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return format(new Date(value), 'MMM d, yyyy, HH:mm')
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return format(new Date(value), 'MMM d, yyyy')
}

/** `3 minutes ago`, `in 2 days`. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return 'never'
  return formatDistanceToNowStrict(new Date(value), { addSuffix: true })
}

const numberFormat = new Intl.NumberFormat('en-US')

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : numberFormat.format(value)
}

/** A path inside the app to go to after login; anything else goes home. */
export function safeNextPath(value: string | null): string {
  if (!value?.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return '/'
  return value
}
