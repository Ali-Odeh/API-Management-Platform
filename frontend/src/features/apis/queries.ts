import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { PAGE_SIZE, wsKey } from '@/features/workspace/keys'

export type ApiSummary = Schema<'ApiSummary'>
export type ApiDetail = Schema<'ApiOut'>
export type ApiStatus = Schema<'ApiStatus'>

export function useApis(slug: string, page: number) {
  return useQuery({
    queryKey: wsKey(slug, 'apis', 'page', page),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/apis', {
          params: { path: { slug }, query: { page, page_size: PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useApi(slug: string, id: string) {
  return useQuery({
    queryKey: wsKey(slug, 'apis', 'one', id),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/apis/{api_id}', {
          params: { path: { slug, api_id: id } },
        }),
      ),
  })
}

export function useApiMutations(slug: string) {
  const queryClient = useQueryClient()
  const onSuccess = () => queryClient.invalidateQueries({ queryKey: wsKey(slug, 'apis') })
  const path = (api_id: string) => ({ path: { slug, api_id } })
  return {
    create: useMutation({
      mutationFn: (body: Schema<'ApiCreate'>) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/apis', { params: { path: { slug } }, body })),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Schema<'ApiUpdate'> }) =>
        unwrap(api.PATCH('/api/v1/workspaces/{slug}/apis/{api_id}', { params: path(id), body })),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(api.DELETE('/api/v1/workspaces/{slug}/apis/{api_id}', { params: path(id) })),
      onSuccess,
    }),
  }
}
