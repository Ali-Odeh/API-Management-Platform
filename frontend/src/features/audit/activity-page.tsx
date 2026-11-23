import { api, unwrap } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import { useWorkspace } from '@/features/workspace/context'
import { wsKey } from '@/features/workspace/keys'
import { AuditLog } from './audit-log'

/** `/w/:slug/activity`: who changed what in this workspace. */
export function ActivityPage() {
  const { slug } = useWorkspace()
  return (
    <>
      <PageHeader
        title="Activity"
        description="Every change in this workspace: who made it, when, and what it changed."
      />
      <AuditLog
        queryKey={wsKey(slug, 'audit')}
        fetchPage={(query) =>
          unwrap(
            api.GET('/api/v1/workspaces/{slug}/audit-events', {
              params: { path: { slug }, query },
            }),
          )
        }
      />
    </>
  )
}
