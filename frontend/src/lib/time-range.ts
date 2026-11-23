import type { Schema } from '@/api/types'

export type RangeKey = '1h' | '24h' | '7d' | '30d'

interface RangeSpec {
  label: string
  ms: number
  interval: Schema<'Interval'>
}

/** The periods the analytics pages offer (the API allows at most 31 days). */
export const RANGES: Record<RangeKey, RangeSpec> = {
  '1h': { label: 'Last hour', ms: 3_600_000, interval: 'minute' },
  '24h': { label: 'Last 24 hours', ms: 86_400_000, interval: 'hour' },
  '7d': { label: 'Last 7 days', ms: 7 * 86_400_000, interval: 'hour' },
  '30d': { label: 'Last 30 days', ms: 30 * 86_400_000, interval: 'day' },
}

/**
 * `from`/`to` for a period ending now, rounded to the minute so the query
 * key stays the same for a minute (and the cache works).
 */
export function rangeQuery(key: RangeKey, now = Date.now()) {
  const spec = RANGES[key]
  const to = Math.ceil(now / 60_000) * 60_000
  return {
    from: new Date(to - spec.ms).toISOString(),
    to: new Date(to).toISOString(),
    interval: spec.interval,
  }
}
