import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Role, Schema } from '@/api/types'
import { meKeys } from '@/features/me/queries'
import { wsKey } from '@/features/workspace/keys'

export type Member = Schema<'MemberOut'>

export function useMembers(slug: string) {
  return useQuery({
    queryKey: wsKey(slug, 'members'),
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces/{slug}/members', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useMemberMutations(slug: string) {
  const queryClient = useQueryClient()
  // My own role may be what changed: reread my workspaces too.
  const onSuccess = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: wsKey(slug, 'members') }),
      queryClient.invalidateQueries({ queryKey: meKeys.workspaces }),
    ])
  const path = (user_id: string) => ({ path: { slug, user_id } })
  return {
    add: useMutation({
      mutationFn: (body: { email: string; role: Role }) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/members', { params: { path: { slug } }, body })),
      onSuccess,
    }),
    changeRole: useMutation({
      mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
        unwrap(
          api.PATCH('/api/v1/workspaces/{slug}/members/{user_id}', {
            params: path(userId),
            body: { role },
          }),
        ),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (userId: string) =>
        unwrap(api.DELETE('/api/v1/workspaces/{slug}/members/{user_id}', { params: path(userId) })),
      onSuccess,
    }),
  }
}
