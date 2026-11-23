import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '@/api/errors'

/** Retrying a 4xx can't help: the request itself was refused. */
export function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 10_000, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
}
