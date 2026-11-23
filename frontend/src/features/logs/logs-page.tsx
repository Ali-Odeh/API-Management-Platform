import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import type { Schema } from '@/api/types'
import { formatMs } from '@/lib/metrics'
import { DataTable, Pager } from '@/components/data-table'
import { Details } from '@/components/details'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useAllConsumers } from '@/features/consumers/queries'
import { useAllRoutes } from '@/features/routes/queries'
import { useWorkspace } from '@/features/workspace/context'
import { wsKey } from '@/features/workspace/keys'
import { formatDateTime, formatNumber } from '@/lib/format'
import { HTTP_METHODS, statusClass } from '@/lib/http'
import { cn } from '@/lib/utils'

type LogEntry = Schema<'RequestLogOut'>
const LOG_PAGE_SIZE = 50
const ANY = 'any'

interface Filters {
  route_id?: string
  consumer_id?: string
  method?: string
  status?: number
}

/** `/w/:slug/logs`: every request the gateway answered, newest first. */
export function LogsPage() {
  const { slug } = useWorkspace()
  const routes = useAllRoutes(slug)
  const consumers = useAllConsumers(slug)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<Filters>({})
  const [statusText, setStatusText] = useState('')
  const [open, setOpen] = useState<LogEntry | null>(null)

  const logs = useQuery({
    queryKey: wsKey(slug, 'logs', filters, page),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/workspaces/{slug}/request-logs', {
          params: { path: { slug }, query: { ...filters, page, page_size: LOG_PAGE_SIZE } },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  function set(next: Filters) {
    setFilters(next)
    setPage(1)
  }

  const routePath = (id: string | null) =>
    id ? (routes.data?.find((r) => r.id === id)?.path_prefix ?? 'deleted route') : '—'
  const consumerName = (id: string | null) =>
    id ? (consumers.data?.find((c) => c.id === id)?.name ?? 'deleted consumer') : '—'

  const picker = (
    label: string,
    value: string | undefined,
    options: { value: string; label: string }[],
    onChange: (value: string | undefined) => void,
  ) => (
    <Select
      value={value ?? ANY}
      onValueChange={(v) => {
        onChange(v === ANY ? undefined : v)
      }}
    >
      <SelectTrigger className="h-8 w-full" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Any {label.toLowerCase()}</SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  return (
    <>
      <PageHeader title="Logs" description="Requests the gateway answered, newest first." />
      <div className="mb-4 grid gap-3 rounded-md border p-3 sm:grid-cols-2 lg:grid-cols-5">
        {picker(
          'Route',
          filters.route_id,
          (routes.data ?? []).map((r) => ({ value: r.id, label: r.path_prefix })),
          (route_id) => {
            set({ ...filters, route_id })
          },
        )}
        {picker(
          'Consumer',
          filters.consumer_id,
          (consumers.data ?? []).map((c) => ({ value: c.id, label: c.name })),
          (consumer_id) => {
            set({ ...filters, consumer_id })
          },
        )}
        {picker(
          'Method',
          filters.method,
          HTTP_METHODS.map((m) => ({ value: m, label: m })),
          (method) => {
            set({ ...filters, method })
          },
        )}
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            const status = Number(statusText)
            set({ ...filters, status: statusText && status >= 100 ? status : undefined })
          }}
        >
          <Label htmlFor="log-status" className="sr-only">
            Status
          </Label>
          <Input
            id="log-status"
            className="h-8"
            inputMode="numeric"
            placeholder="Status, e.g. 429"
            value={statusText}
            onChange={(e) => {
              setStatusText(e.target.value)
            }}
          />
        </form>
        <Button
          variant="ghost"
          size="sm"
          className="justify-self-start"
          onClick={() => {
            setStatusText('')
            set({})
          }}
        >
          <X /> Clear
        </Button>
      </div>
      <DataTable<LogEntry>
        rows={logs.data?.items}
        isPending={logs.isPending}
        error={logs.error}
        onRetry={() => void logs.refetch()}
        rowKey={(l) => l.event_id}
        onRowClick={setOpen}
        rowLabel={(l) => `Details of ${l.method} ${l.path}`}
        empty={{
          title: 'No requests',
          description: 'Requests appear here a few seconds after the gateway answers them.',
        }}
        columns={[
          { header: 'Time', cell: (l) => formatDateTime(l.ts), className: 'whitespace-nowrap' },
          {
            header: 'Status',
            cell: (l) => (
              <span className={cn('font-mono font-semibold', statusClass(l.status))}>
                {l.status}
              </span>
            ),
          },
          {
            header: 'Request',
            cell: (l) => (
              <span className="font-mono text-xs">
                <Badge variant="secondary" className="mr-1 font-mono text-[10px]">
                  {l.method}
                </Badge>
                {l.path}
              </span>
            ),
            className: 'max-w-80 truncate',
          },
          { header: 'Consumer', cell: (l) => consumerName(l.consumer_id) },
          { header: 'Latency', cell: (l) => formatMs(l.latency_ms), className: 'text-right' },
        ]}
      />
      {logs.data && (
        <Pager page={page} pageSize={LOG_PAGE_SIZE} total={logs.data.total} onPage={setPage} />
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
                <SheetTitle className="font-mono break-all">
                  {open.method} {open.path}
                </SheetTitle>
                <SheetDescription>{formatDateTime(open.ts)}</SheetDescription>
              </SheetHeader>
              <div className="px-4 pb-6">
                <Details
                  items={[
                    [
                      'Status',
                      <span key="s" className={cn('font-mono', statusClass(open.status))}>
                        {open.status}
                      </span>,
                    ],
                    ['Route', routePath(open.route_id)],
                    ['Consumer', consumerName(open.consumer_id)],
                    ['Rate limit', open.ratelimit ?? '—'],
                    ['Latency', formatMs(open.latency_ms)],
                    ['Upstream time', formatMs(open.upstream_ms)],
                    ['Response size', `${formatNumber(open.bytes)} bytes`],
                    ['Client IP', open.client_ip ?? '—'],
                    [
                      'Request id',
                      <code key="r" className="text-xs">
                        {open.request_id ?? '—'}
                      </code>,
                    ],
                  ]}
                />
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  )
}
