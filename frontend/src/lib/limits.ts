import type { Schema } from '@/api/types'

export type LimitName = keyof Schema<'Limits'>

/** A workspace's limits as people read them. */
export const LIMIT_LABELS: [LimitName, string][] = [
  ['rate_limit_per_min', 'Requests per minute'],
  ['max_concurrent', 'Requests in flight'],
  ['ip_rate_limit_per_min', 'Requests per minute per client IP (public routes)'],
  ['max_services', 'Services'],
  ['max_routes', 'Routes'],
  ['max_consumers', 'Consumers'],
]
