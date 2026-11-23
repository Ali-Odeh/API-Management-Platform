import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchAll, unwrap } from '@/api/client'
import type { Schema, WorkspaceStatus } from '@/api/types'
import { meKeys } from '@/features/me/queries'

export type PlatformWorkspace = Schema<'WorkspaceOut'>
export type PlatformUser = Schema<'UserListItem'>
export type Developer = Schema<'DeveloperOut'>

export const PLATFORM_PAGE_SIZE = 25

export const platformKeys = {
  workspaces: ['platform', 'workspaces'] as const,
  users: ['platform', 'users'] as const,
  developers: ['platform', 'developers'] as const,
  audit: ['platform', 'audit'] as const,
}

export function useAllWorkspaces(page: number, status?: WorkspaceStatus) {
  return useQuery({
    queryKey: [...platformKeys.workspaces, 'page', page, status ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces', {
          params: { query: { page, page_size: PLATFORM_PAGE_SIZE, status } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

/** Every workspace (for names in the platform's audit log and its filter). */
export function useWorkspaceDirectory() {
  return useQuery({
    queryKey: [...platformKeys.workspaces, 'all'],
    queryFn: () =>
      fetchAll((page) =>
        api.GET('/api/v1/workspaces', { params: { query: { page, page_size: 100 } } }),
      ),
    staleTime: 60_000,
  })
}

/** Lifecycle changes of a workspace: each one rereads the lists and my own workspaces. */
export function useWorkspaceLifecycle() {
  const queryClient = useQueryClient()
  const onSuccess = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: platformKeys.workspaces }),
      queryClient.invalidateQueries({ queryKey: meKeys.workspaces }),
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
    ])
  const path = (slug: string) => ({ path: { slug } })
  return {
    create: useMutation({
      mutationFn: (body: Schema<'WorkspaceCreate'>) =>
        unwrap(api.POST('/api/v1/workspaces', { body })),
      onSuccess,
    }),
    suspend: useMutation({
      mutationFn: ({ slug, reason }: { slug: string; reason: string }) =>
        unwrap(
          api.POST('/api/v1/workspaces/{slug}/suspend', { params: path(slug), body: { reason } }),
        ),
      onSuccess,
    }),
    unsuspend: useMutation({
      mutationFn: (slug: string) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/unsuspend', { params: path(slug) })),
      onSuccess,
    }),
    restore: useMutation({
      mutationFn: (slug: string) =>
        unwrap(api.POST('/api/v1/workspaces/{slug}/restore', { params: path(slug) })),
      onSuccess,
    }),
    remove: useMutation({
      mutationFn: (slug: string) =>
        unwrap(
          api.DELETE('/api/v1/workspaces/{slug}', { params: path(slug), body: { confirm: slug } }),
        ),
      onSuccess,
    }),
    limits: useMutation({
      mutationFn: ({ slug, body }: { slug: string; body: Schema<'LimitsUpdate'> }) =>
        unwrap(api.PATCH('/api/v1/workspaces/{slug}/limits', { params: path(slug), body })),
      onSuccess,
    }),
  }
}

export function useUsers(page: number, filters: { is_active?: boolean; email?: string }) {
  return useQuery({
    queryKey: [...platformKeys.users, page, filters],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/users', {
          params: { query: { page, page_size: PLATFORM_PAGE_SIZE, ...filters } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useSetUserActive() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      unwrap(
        api.PATCH('/api/v1/users/{user_id}', {
          params: { path: { user_id: id } },
          body: { is_active: isActive },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformKeys.users }),
  })
}

export function useDevelopers(page: number, status?: Schema<'DeveloperStatus'>) {
  return useQuery({
    queryKey: [...platformKeys.developers, page, status ?? null],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/developers', {
          params: { query: { page, page_size: PLATFORM_PAGE_SIZE, status } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useSetDeveloperStatus() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: Schema<'DeveloperStatus'> }) =>
      unwrap(
        api.PATCH('/api/v1/developers/{developer_id}', {
          params: { path: { developer_id: id } },
          body: { status },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: platformKeys.developers }),
  })
}
