/**
 * Query keys of one workspace's data: `['w', slug, resource, ...]`. A
 * write invalidates its resource (`wsKey(slug, 'services')`), which also
 * covers that resource's lists and details.
 */
export function wsKey(slug: string, ...parts: unknown[]): unknown[] {
  return ['w', slug, ...parts]
}

/** Page size of the dashboard's tables. */
export const PAGE_SIZE = 25
