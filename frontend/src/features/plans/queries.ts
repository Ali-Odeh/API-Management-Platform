import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { wsKey } from '@/features/workspace/keys'
import { formatNumber } from '@/lib/format'

export type Plan = Schema<'PlanOut'>
export type PlanCreate = Schema<'PlanCreate'>

/** Every plan of the workspace (a handful), default first then by name. */
export function usePlans(slug: string) {
  return useQuery({
    queryKey: wsKey(slug, 'plans'),
    queryFn: async () => {
      const plans = await fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/plans', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      )
      return plans.sort(
        (a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name),
      )
    },
  })
}

export function usePlanMutations(slug: string) {
  const queryClient = useQueryClient()
  // A default plan change moves other plans' flags: reread them all.
  const onSuccess = () => queryClient.invalidateQueries({ queryKey: wsKey(slug, 'plans') })
  const path = (plan_id: string) => ({ path: { slug, plan_id } })
  return {
    create: useMutation({
      mutationFn: (body: PlanCreate) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/plans', { params: { path: { slug } }, body })),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Schema<'PlanUpdate'> }) =>
        unwrap(api.PATCH('/api/v1/workspaces/{slug}/plans/{plan_id}', { params: path(id), body })),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(api.DELETE('/api/v1/workspaces/{slug}/plans/{plan_id}', { params: path(id) })),
      onSuccess,
    }),
  }
}

/** `Gold (600/min, 100,000/month)`: a plan in a picker. */
export function planLabel(plan: Plan): string {
  const quota = plan.monthly_quota ? `, ${formatNumber(plan.monthly_quota)}/month` : ''
  return `${plan.name} (${formatNumber(plan.rate_limit_per_min)}/min${quota})`
}
