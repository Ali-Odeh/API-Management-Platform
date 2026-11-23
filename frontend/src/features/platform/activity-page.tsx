import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { AuditLog } from '@/features/audit/audit-log'
import { platformKeys, useWorkspaceDirectory } from './queries'

const EVERYWHERE = 'all'

/** `/platform/activity`: every audit event, platform-level and of every workspace. */
export function PlatformActivityPage() {
  const directory = useWorkspaceDirectory()
  const [workspaceId, setWorkspaceId] = useState(EVERYWHERE)
  const scope = workspaceId === EVERYWHERE ? undefined : workspaceId
  const nameOf = (id: string | null) => {
    if (id === null) return <span className="text-muted-foreground">Platform</span>
    return directory.data?.find((w) => w.id === id)?.slug ?? id.slice(0, 8)
  }

  return (
    <>
      <PageHeader
        title="Platform activity"
        description="Workspaces created, suspended, deleted and restored, people disabled, and every workspace's own changes."
        actions={
          <Select value={workspaceId} onValueChange={setWorkspaceId}>
            <SelectTrigger className="w-52" aria-label="Workspace">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={EVERYWHERE}>All workspaces</SelectItem>
              {directory.data?.map((w) => (
                <SelectItem key={w.id} value={w.id}>
                  {w.name} ({w.slug})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <AuditLog
        queryKey={[...platformKeys.audit, scope ?? null]}
        showWorkspace={nameOf}
        fetchPage={(query) =>
          unwrap(
            api.GET('/api/v1/audit-events', {
              params: { query: { ...query, workspace_id: scope } },
            }),
          )
        }
      />
    </>
  )
}
