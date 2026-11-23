import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { PAGE_SIZE, wsKey } from '@/features/workspace/keys'
import { workspaceKeys } from '@/features/workspace/queries'

export type Consumer = Schema<'ConsumerOut'>
export type ConsumerStatus = Schema<'ConsumerStatus'>
export type Key = Schema<'KeyOut'>
export type KeyCreated = Schema<'KeyCreated'>

export function useConsumers(slug: string, page: number, status?: ConsumerStatus) {
  return useQuery({
    queryKey: wsKey(slug, 'consumers', 'page', page, status ?? null),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/consumers', {
          params: { path: { slug }, query: { page, page_size: PAGE_SIZE, status } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

/** Every consumer, for pickers (log filters). */
export function useAllConsumers(slug: string) {
  return useQuery({
    queryKey: wsKey(slug, 'consumers', 'all'),
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/consumers', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useConsumer(slug: string, id: string) {
  return useQuery({
    queryKey: wsKey(slug, 'consumers', 'one', id),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/consumers/{consumer_id}', {
          params: { path: { slug, consumer_id: id } },
        }),
      ),
  })
}

export function useKeys(slug: string, consumerId: string) {
  return useQuery({
    queryKey: wsKey(slug, 'keys', consumerId),
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/consumers/{consumer_id}/keys', {
          params: { path: { slug, consumer_id: consumerId }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useConsumerMutations(slug: string) {
  const queryClient = useQueryClient()
  const onSuccess = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: wsKey(slug, 'consumers') }),
      queryClient.invalidateQueries({ queryKey: workspaceKeys.detail(slug) }),
    ])
  const path = (consumer_id: string) => ({ path: { slug, consumer_id } })
  return {
    create: useMutation({
      mutationFn: (body: Schema<'ConsumerCreate'>) =>
        unwrap(
          api.POST('/api/v1/workspaces/{slug}/consumers', { params: { path: { slug } }, body }),
        ),
      onSuccess,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: Schema<'ConsumerUpdate'> }) =>
        unwrap(
          api.PATCH('/api/v1/workspaces/{slug}/consumers/{consumer_id}', {
            params: path(id),
            body,
          }),
        ),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(
          api.DELETE('/api/v1/workspaces/{slug}/consumers/{consumer_id}', { params: path(id) }),
        ),
      onSuccess,
    }),
  }
}

export function useKeyMutations(slug: string, consumerId: string) {
  const queryClient = useQueryClient()
  const onSuccess = () =>
    queryClient.invalidateQueries({ queryKey: wsKey(slug, 'keys', consumerId) })
  return {
    create: useMutation({
      mutationFn: (name: string) =>
        unwrap(
          api.POST('/api/v1/workspaces/{slug}/consumers/{consumer_id}/keys', {
            params: { path: { slug, consumer_id: consumerId } },
            body: { name },
          }),
        ),
      onSuccess,
    }),
    revoke: useMutation({
      mutationFn: (keyId: string) =>
        unwrap(
          api.DELETE('/api/v1/workspaces/{slug}/keys/{key_id}', {
            params: { path: { slug, key_id: keyId } },
          }),
        ),
      onSuccess,
    }),
  }
}
