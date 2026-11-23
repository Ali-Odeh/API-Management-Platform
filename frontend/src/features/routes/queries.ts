import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { PAGE_SIZE, wsKey } from '@/features/workspace/keys'
import { workspaceKeys } from '@/features/workspace/queries'

export type Route = Schema<'RouteOut'>
export type RouteCreate = Schema<'RouteCreate'>
export type RouteUpdate = Schema<'RouteUpdate'>

export function useRoutes(slug: string, page: number, serviceId?: string) {
  return useQuery({
    queryKey: wsKey(slug, 'routes', 'page', page, serviceId ?? null),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/routes', {
          params: {
            path: { slug },
            query: { page, page_size: PAGE_SIZE, service_id: serviceId },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

/** Every route, for pickers (an API's routes, log filters). */
export function useAllRoutes(slug: string) {
  return useQuery({
    queryKey: wsKey(slug, 'routes', 'all'),
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/routes', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useRouteMutations(slug: string) {
  const queryClient = useQueryClient()
  const onSuccess = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: wsKey(slug, 'routes') }),
      queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(slug) }),
    ])
  const path = (route_id: string) => ({ path: { slug, route_id } })
  return {
    create: useMutation({
      mutationFn: (body: RouteCreate) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/routes', { params: { path: { slug } }, body })),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: RouteUpdate }) =>
        unwrap(
          api.PATCH('/api/v1/workspaces/{slug}/routes/{route_id}', { params: path(id), body }),
        ),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(api.DELETE('/api/v1/workspaces/{slug}/routes/{route_id}', { params: path(id) })),
      onSuccess,
    }),
  }
}
