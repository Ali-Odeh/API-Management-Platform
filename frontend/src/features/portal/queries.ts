import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchAll, portalApi, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { rangeQuery, type RangeKey } from '@/lib/time-range'

export type CatalogWorkspace = Schema<'CatalogWorkspaceOut'>
export type CatalogApiSummary = Schema<'CatalogApiSummary'>
export type CatalogApi = Schema<'CatalogApi'>
export type OfferedPlan = Schema<'OfferedPlan'>
export type App = Schema<'AppOut'>

/** Portal data lives under its own key: it belongs to the developer, not a member. */
export const portalKeys = {
  me: ['portal', 'me'] as const,
  workspaces: ['portal', 'workspaces'] as const,
  apis: (slug: string) => ['portal', 'apis', slug] as const,
  api: (slug: string, id: string) => ['portal', 'api', slug, id] as const,
  plans: (slug: string) => ['portal', 'plans', slug] as const,
  apps: ['portal', 'apps'] as const,
  app: (id: string) => ['portal', 'apps', id] as const,
  keys: (id: string) => ['portal', 'apps', id, 'keys'] as const,
  usage: (id: string, range: RangeKey) => ['portal', 'apps', id, 'usage', range] as const,
}

/** The signed-in developer; their row is made on the first call. */
export function usePortalMe() {
  return useQuery({
    queryKey: portalKeys.me,
    queryFn: () => unwrap(portalApi.GET('/api/v1/portal/me')),
    staleTime: 60_000,
  })
}

/** The active workspaces that publish at least one API. */
export function useCatalogWorkspaces() {
  return useQuery({
    queryKey: portalKeys.workspaces,
    queryFn: () =>
      fetchAll((page) =>
        portalApi.GET('/api/v1/portal/workspaces', { params: { query: { page, page_size: 100 } } }),
      ),
  })
}

export function useCatalogApis(slug: string) {
  return useQuery({
    queryKey: portalKeys.apis(slug),
    queryFn: () =>
      fetchAll((page) =>
        portalApi.GET('/api/v1/portal/workspaces/{slug}/apis', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
  })
}

export function useCatalogApi(slug: string, id: string) {
  return useQuery({
    queryKey: portalKeys.api(slug, id),
    queryFn: () =>
      unwrap(
        portalApi.GET('/api/v1/portal/workspaces/{slug}/apis/{api_id}', {
          params: { path: { slug, api_id: id } },
        }),
      ),
  })
}

export function useOfferedPlans(slug: string, enabled = true) {
  return useQuery({
    queryKey: portalKeys.plans(slug),
    queryFn: () =>
      fetchAll((page) =>
        portalApi.GET('/api/v1/portal/workspaces/{slug}/plans', {
          params: { path: { slug }, query: { page, page_size: 100 } },
        }),
      ),
    enabled,
  })
}

export function useMyApps() {
  return useQuery({
    queryKey: portalKeys.apps,
    queryFn: () =>
      fetchAll((page) =>
        portalApi.GET('/api/v1/portal/apps', { params: { query: { page, page_size: 100 } } }),
      ),
  })
}

export function useMyApp(id: string) {
  return useQuery({
    queryKey: portalKeys.app(id),
    queryFn: () =>
      unwrap(portalApi.GET('/api/v1/portal/apps/{app_id}', { params: { path: { app_id: id } } })),
  })
}

export function useAppKeys(id: string, enabled: boolean) {
  return useQuery({
    queryKey: portalKeys.keys(id),
    queryFn: () =>
      fetchAll((page) =>
        portalApi.GET('/api/v1/portal/apps/{app_id}/keys', {
          params: { path: { app_id: id }, query: { page, page_size: 100 } },
        }),
      ),
    enabled,
  })
}

export function useAppUsage(id: string, range: RangeKey) {
  return useQuery({
    queryKey: portalKeys.usage(id, range),
    queryFn: () =>
      unwrap(
        portalApi.GET('/api/v1/portal/apps/{app_id}/usage', {
          params: { path: { app_id: id }, query: rangeQuery(range) },
        }),
      ),
  })
}

export function useAppMutations() {
  const queryClient = useQueryClient()
  const onSuccess = () => queryClient.invalidateQueries({ queryKey: portalKeys.apps })
  return {
    create: useMutation({
      mutationFn: (body: Schema<'AppCreate'>) =>
        unwrap(portalApi.POST('/api/v1/portal/apps', { body })),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (id: string) =>
        unwrap(
          portalApi.DELETE('/api/v1/portal/apps/{app_id}', { params: { path: { app_id: id } } }),
        ),
      onSuccess,
    }),
    createKey: useMutation({
      mutationFn: ({ id, name }: { id: string; name: string }) =>
        unwrap(
          portalApi.POST('/api/v1/portal/apps/{app_id}/keys', {
            params: { path: { app_id: id } },
            body: { name },
          }),
        ),
      onSuccess,
    }),
    revokeKey: useMutation({
      mutationFn: ({ id, keyId }: { id: string; keyId: string }) =>
        unwrap(
          portalApi.DELETE('/api/v1/portal/apps/{app_id}/keys/{key_id}', {
            params: { path: { app_id: id, key_id: keyId } },
          }),
        ),
      onSuccess,
    }),
  }
}
