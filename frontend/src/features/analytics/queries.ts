import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import { wsKey } from '@/features/workspace/keys'
import { rangeQuery, type RangeKey } from '@/lib/time-range'

/** Requests over time, optionally of one route or consumer. */
export function useTraffic(
  slug: string,
  range: RangeKey,
  filter: { route_id?: string; consumer_id?: string } = {},
) {
  const query = { ...rangeQuery(range), ...filter }
  return useQuery({
    queryKey: wsKey(slug, 'analytics', 'traffic', query),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/analytics/traffic', {
          params: { path: { slug }, query },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useTopRoutes(slug: string, range: RangeKey, limit = 5) {
  const { from, to } = rangeQuery(range)
  return useQuery({
    queryKey: wsKey(slug, 'analytics', 'routes', from, to, limit),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/analytics/routes', {
          params: { path: { slug }, query: { from, to, limit } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useTopConsumers(slug: string, range: RangeKey, limit = 5) {
  const { from, to } = rangeQuery(range)
  return useQuery({
    queryKey: wsKey(slug, 'analytics', 'consumers', from, to, limit),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/analytics/consumers', {
          params: { path: { slug }, query: { from, to, limit } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}
