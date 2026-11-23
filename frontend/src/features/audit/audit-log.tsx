import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useState } from 'react'
import type { Schema } from '@/api/types'
import { DataTable, Pager } from '@/components/data-table'
import { Details, JsonBlock } from '@/components/details'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { formatDateTime } from '@/lib/format'

export type AuditEvent = Schema<'AuditEventOut'>
type Page = Schema<'Page_AuditEventOut_'>

export interface AuditFilters {
  actor?: string
  action?: string
  target_type?: string
  /** ISO times; `to` is exclusive. */
  from?: string
  to?: string
}

const AUDIT_PAGE_SIZE = 50

const ACTOR_LABEL: Record<Schema<'ActorType'>, string> = {
  user: 'Member',
  platform_admin: 'Platform Admin',
  system: 'System',
  developer: 'Developer',
}

/** A date input's day as the start of that day, local time, in ISO. */
function dayStart(day: string, plusDays = 0): string | undefined {
  if (!day) return undefined
  const date = new Date(`${day}T00:00:00`)
  date.setDate(date.getDate() + plusDays)
  return date.toISOString()
}

/**
 * The audit log with filters and a details panel. The workspace's
 * Activity page and the platform's use it, each with its own source.
 */
export function AuditLog({
  queryKey,
  fetchPage,
  showWorkspace,
}: {
  queryKey: unknown[]
  fetchPage: (query: AuditFilters & { page: number; page_size: number }) => Promise<Page>
  /** Platform-wide lists show which workspace an event belongs to. */
  showWorkspace?: (workspaceId: string | null) => React.ReactNode
}) {
  const [page, setPage] = useState(1)
  const [draft, setDraft] = useState({ actor: '', action: '', target_type: '', from: '', to: '' })
  const [filters, setFilters] = useState<AuditFilters>({})
  const [open, setOpen] = useState<AuditEvent | null>(null)

  const events = useQuery({
    queryKey: [...queryKey, filters, page],
    queryFn: () => fetchPage({ ...filters, page, page_size: AUDIT_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })

  function apply() {
    setFilters({
      actor: draft.actor.trim() || undefined,
      action: draft.action.trim() || undefined,
      target_type: draft.target_type.trim() || undefined,
      from: dayStart(draft.from),
      // The whole "to" day counts.
      to: dayStart(draft.to, 1),
    })
    setPage(1)
  }

  function clear() {
    setDraft({ actor: '', action: '', target_type: '', from: '', to: '' })
    setFilters({})
    setPage(1)
  }

  const field = (
    key: keyof typeof draft,
    label: string,
    props: React.ComponentProps<'input'> = {},
  ) => (
    <div className="grid gap-1">
      <Label htmlFor={`audit-${key}`} className="text-xs">
        {label}
      </Label>
      <Input
        id={`audit-${key}`}
        className="h-8"
        value={draft[key]}
        onChange={(e) => {
          setDraft({ ...draft, [key]: e.target.value })
        }}
        {...props}
      />
    </div>
  )

  return (
    <>
      <form
        className="mb-4 grid gap-3 rounded-md border p-3 sm:grid-cols-3 lg:grid-cols-6"
        onSubmit={(e) => {
          e.preventDefault()
          apply()
        }}
      >
        {field('actor', 'Actor email', { placeholder: 'ana@example.com' })}
        {field('action', 'Action', { placeholder: 'route.update' })}
        {field('target_type', 'Target type', { placeholder: 'route' })}
        {field('from', 'From', { type: 'date' })}
        {field('to', 'To', { type: 'date' })}
        <div className="flex items-end gap-2">
          <Button type="submit" size="sm">
            Filter
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={clear}
            aria-label="Clear filters"
          >
            <X />
          </Button>
        </div>
      </form>
      <DataTable<AuditEvent>
        rows={events.data?.items}
        isPending={events.isPending}
        error={events.error}
        onRetry={() => void events.refetch()}
        rowKey={(e) => e.id}
        onRowClick={setOpen}
        rowLabel={(e) => `Details of ${e.action} at ${formatDateTime(e.created_at)}`}
        empty={{ title: 'No events', description: 'Nothing matches these filters.' }}
        columns={[
          {
            header: 'When',
            cell: (e) => formatDateTime(e.created_at),
            className: 'whitespace-nowrap',
          },
          {
            header: 'Who',
            cell: (e) => (
              <span className="flex flex-col">
                <span>{e.actor_email ?? ACTOR_LABEL[e.actor_type]}</span>
                {e.actor_type !== 'user' && (
                  <span className="text-muted-foreground text-xs">{ACTOR_LABEL[e.actor_type]}</span>
                )}
              </span>
            ),
          },
          {
            header: 'Action',
            cell: (e) => (
              <Badge variant="secondary" className="font-mono">
                {e.action}
              </Badge>
            ),
          },
          {
            header: 'Target',
            cell: (e) =>
              e.target_name ?? <span className="text-muted-foreground">{e.target_type}</span>,
          },
          ...(showWorkspace
            ? [{ header: 'Workspace', cell: (e: AuditEvent) => showWorkspace(e.workspace_id) }]
            : []),
        ]}
      />
      {events.data && (
        <Pager page={page} pageSize={AUDIT_PAGE_SIZE} total={events.data.total} onPage={setPage} />
      )}
      <Sheet
        open={open !== null}
        onOpenChange={(next) => {
          if (!next) setOpen(null)
        }}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {open && (
            <>
              <SheetHeader>
                <SheetTitle className="font-mono">{open.action}</SheetTitle>
                <SheetDescription>{formatDateTime(open.created_at)}</SheetDescription>
              </SheetHeader>
              <div className="grid gap-4 px-4 pb-6">
                <Details
                  items={[
                    ['Actor', `${open.actor_email ?? '—'} (${ACTOR_LABEL[open.actor_type]})`],
                    ['Target', `${open.target_type} ${open.target_name ?? ''}`],
                    [
                      'Target id',
                      <code key="t" className="text-xs">
                        {open.target_id ?? '—'}
                      </code>,
                    ],
                    ['IP address', open.ip ?? '—'],
                    ['User agent', open.user_agent ?? '—'],
                    [
                      'Request id',
                      <code key="r" className="text-xs">
                        {open.request_id ?? '—'}
                      </code>,
                    ],
                  ]}
                />
                <div className="grid gap-2">
                  <p className="text-sm font-medium">Changes</p>
                  <JsonBlock value={open.changes} />
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  )
}
