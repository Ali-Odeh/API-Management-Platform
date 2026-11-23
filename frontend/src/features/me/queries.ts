import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'

export const meKeys = {
  me: ['me'] as const,
  workspaces: ['me', 'workspaces'] as const,
}

/** The signed-in person (`is_platform_admin`, `is_active`). */
export function useMe() {
  return useQuery({
    queryKey: meKeys.me,
    queryFn: () => unwrap(api.GET('/api/v1/me')),
    staleTime: 60_000,
  })
}

/** The workspaces I am a member of, with my role in each. */
export function useMyWorkspaces() {
  return useQuery({
    queryKey: meKeys.workspaces,
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/me/workspaces', { params: { query: { page, page_size: 100 } } }),
      ),
  })
}

export function useLeaveWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) =>
      unwrap(api.DELETE('/api/v1/me/workspaces/{slug}', { params: { path: { slug } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meKeys.workspaces }),
  })
}
