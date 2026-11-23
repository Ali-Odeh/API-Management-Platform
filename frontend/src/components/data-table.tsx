import { ChevronLeft, ChevronRight } from 'lucide-react'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface Column<T> {
  header: React.ReactNode
  cell: (row: T) => React.ReactNode
  className?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[] | undefined
  rowKey: (row: T) => string
  isPending?: boolean
  error?: unknown
  onRetry?: () => void
  empty: { title: string; description?: string; action?: React.ReactNode }
  /** Makes rows clickable (also by keyboard). */
  onRowClick?: (row: T) => void
  /** A label for each row's click, for screen readers. */
  rowLabel?: (row: T) => string
}

/** A table with its loading, error and empty states. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  isPending,
  error,
  onRetry,
  empty,
  onRowClick,
  rowLabel,
}: DataTableProps<T>) {
  if (isPending) return <LoadingState />
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (!rows?.length) return <EmptyState {...empty} />
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column, i) => (
              <TableHead key={i} className={column.className}>
                {column.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={rowKey(row)}
              className={cn(onRowClick && 'cursor-pointer')}
              tabIndex={onRowClick ? 0 : undefined}
              aria-label={rowLabel?.(row)}
              onClick={
                onRowClick &&
                (() => {
                  onRowClick(row)
                })
              }
              onKeyDown={
                onRowClick &&
                ((e) => {
                  if (e.key === 'Enter') onRowClick(row)
                })
              }
            >
              {columns.map((column, i) => (
                <TableCell key={i} className={column.className}>
                  {column.cell(row)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** Previous / next for a server-paged list. Pages start at 1. */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  if (total <= pageSize) return null
  return (
    <div className="text-muted-foreground mt-3 flex items-center justify-end gap-2 text-sm">
      <span>
        Page {page} of {pages} · {formatNumber(total)} in all
      </span>
      <Button
        variant="outline"
        size="icon"
        aria-label="Previous page"
        disabled={page <= 1}
        onClick={() => {
          onPage(page - 1)
        }}
      >
        <ChevronLeft />
      </Button>
      <Button
        variant="outline"
        size="icon"
        aria-label="Next page"
        disabled={page >= pages}
        onClick={() => {
          onPage(page + 1)
        }}
      >
        <ChevronRight />
      </Button>
    </div>
  )
}
