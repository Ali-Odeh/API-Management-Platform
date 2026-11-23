import { createContext, useContext } from 'react'
import type { WorkspaceDetail } from '@/api/types'
import { decide, type Access, type Decision, type Permission } from '@/lib/permissions'

export interface WorkspaceContextValue {
  slug: string
  workspace: WorkspaceDetail
  access: Access
  can: (permission: Permission) => Decision
}

export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function makeWorkspaceContext(
  workspace: WorkspaceDetail,
  access: Access,
): WorkspaceContextValue {
  return {
    slug: workspace.slug,
    workspace,
    access,
    can: (permission) => decide(access, permission),
  }
}

/** The workspace of the current `/w/:slug` page and what I may do in it. */
export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error('useWorkspace outside a workspace page')
  return value
}
