import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/api/client'

export const workspaceKeys = {
  all: ['workspaces'] as const,
  detail: (slug: string) => ['workspaces', slug] as const,
}

/** The workspace's details, limits and usage counts. */
export function useWorkspaceDetail(slug: string) {
  return useQuery({
    queryKey: workspaceKeys.detail(slug),
    queryFn: () => unwrap(api.GET('/api/v1/workspaces/{slug}', { params: { path: { slug } } })),
  })
}
