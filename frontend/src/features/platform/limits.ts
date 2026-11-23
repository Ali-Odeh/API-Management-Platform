import type { Schema } from '@/api/types'
import { formatNumber } from '@/lib/format'
import { LIMIT_LABELS, type LimitName } from '@/lib/limits'
import type { PlatformWorkspace } from './queries'

/** The bounds of PATCH /limits (PLAN.md section 6). */
const MAX: Record<LimitName, number> = {
  rate_limit_per_min: 1_000_000,
  max_concurrent: 10_000,
  ip_rate_limit_per_min: 1_000_000,
  max_services: 10_000,
  max_routes: 100_000,
  max_consumers: 1_000_000,
}

export type Draft = Record<LimitName, string>

export function toDraft(workspace: PlatformWorkspace): Draft {
  const draft = {} as Draft
  for (const [name] of LIMIT_LABELS) {
    const own = workspace.limit_overrides[name]
    draft[name] = own === null || own === undefined ? '' : String(own)
  }
  return draft
}

/** Which limits to send: changed ones only; empty puts one back to the default (null). */
export function limitChanges(
  workspace: PlatformWorkspace,
  draft: Draft,
): { body: Schema<'LimitsUpdate'> } | { error: string } {
  const body: Schema<'LimitsUpdate'> = {}
  for (const [name, label] of LIMIT_LABELS) {
    const text = draft[name].trim()
    const own = workspace.limit_overrides[name] ?? null
    if (text === '') {
      if (own !== null) body[name] = null
      continue
    }
    const value = Number(text)
    if (!Number.isInteger(value) || value < 1 || value > MAX[name]) {
      return { error: `${label}: a whole number from 1 to ${formatNumber(MAX[name])}` }
    }
    if (value !== own) body[name] = value
  }
  return { body }
}
