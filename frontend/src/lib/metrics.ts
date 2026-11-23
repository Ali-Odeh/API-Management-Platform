/** The error share of some requests, as `1.2%`. */
export function errorRate(counts: {
  requests: number
  client_errors: number
  server_errors: number
}): string {
  if (counts.requests === 0) return '—'
  const rate = ((counts.client_errors + counts.server_errors) / counts.requests) * 100
  return `${rate.toFixed(rate < 10 ? 1 : 0)}%`
}

/** A duration in milliseconds: one decimal below 10 ms. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 0.1) return '< 0.1 ms'
  return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`
}
