import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { PAGE_SIZE, wsKey } from '@/features/workspace/keys'
import { workspaceKeys } from '@/features/workspace/queries'

export type Service = Schema<'ServiceOut'>
export type ServiceCreate = Schema<'ServiceCreate'>
export type ServiceUpdate = Schema<'ServiceUpdate'>
export type Target = Schema<'TargetOut'>

export function useServices(slug: string, page: number) {
  return useQuery({
    queryKey: wsKey(slug, 'services', 'page', page),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/services', {
          params: { path: { slug }, query: { page, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

/** Every service, for pickers (a route's service). */
export function useAllServices(slug: string) {
  return useQuery({
    queryKey: wsKey(slug, 'services', 'all'),
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/services', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useService(slug: string, id: string) {
  return useQuery({
    queryKey: wsKey(slug, 'services', 'one', id),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/services/{service_id}', {
          params: { path: { slug, service_id: id } },
        }),
      ),
  })
}

/** Writes on services: each refreshes the services and the workspace's counts. */
export function useServiceMutations(slug: string) {
  const queryClient = useQueryClient()
  const onSuccess = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: wsKey(slug, 'services') }),
      queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(slug) }),
    ])
  const path = (service_id: string) => ({ path: { slug, service_id } })

  return {
    create: useMutation({
      mutationFn: (body: ServiceCreate) =>
        unwrap(
          api.POST('/api/v1/workspaces/{slug}/services', { params: { path: { slug } }, body }),
        ),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: ServiceUpdate }) =>
        unwrap(
          api.PATCH('/api/v1/workspaces/{slug}/services/{service_id}', { params: path(id), body }),
        ),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(api.DELETE('/api/v1/workspaces/{slug}/services/{service_id}', { params: path(id) })),
      onSuccess,
    }),
    addTarget: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Schema<'TargetCreate'> }) =>
        unwrap(
          api.POST('/api/v1/workspaces/{slug}/services/{service_id}/targets', {
            params: path(id),
            body,
          }),
        ),
      onSuccess,
    }),
    updateTarget: useMutation({
      mutationFn: ({
        id,
        targetId,
        body,
      }: {
        id: string
        targetId: string
        body: Schema<'TargetUpdate'>
      }) =>
        unwrap(
          api.PATCH('/api/v1/workspaces/{slug}/services/{service_id}/targets/{target_id}', {
            params: { path: { slug, service_id: id, target_id: targetId } },
            body,
          }),
        ),
      onSuccess,
    }),
    removeTarget: useMutation({
      mutationFn: ({ id, targetId }: { id: string; targetId: string }) =>
        unwrap(
          api.DELETE('/api/v1/workspaces/{slug}/services/{service_id}/targets/{target_id}', {
            params: { path: { slug, service_id: id, target_id: targetId } },
          }),
        ),
      onSuccess,
    }),
  }
}
