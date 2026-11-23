import { format } from 'date-fns'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Schema } from '@/api/types'
import { EmptyState } from '@/components/states'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { formatNumber } from '@/lib/format'
import { RANGES, type RangeKey } from '@/lib/time-range'

type Traffic = Schema<'TrafficOut'>

const TICK_FORMAT: Record<Schema<'Interval'>, string> = {
  minute: 'HH:mm',
  hour: 'MMM d HH:mm',
  day: 'MMM d',
}

/** Requests and errors per bucket of a traffic answer. */
export function TrafficChart({ traffic, height = 240 }: { traffic: Traffic; height?: number }) {
  if (traffic.totals.requests === 0) {
    return <EmptyState title="No requests in this period" />
  }
  const data = traffic.buckets.map((b) => ({
    start: b.start,
    requests: b.requests,
    errors: b.client_errors + b.server_errors,
  }))
  const tick = TICK_FORMAT[traffic.interval]
  return (
    <div style={{ height }} role="img" aria-label="Requests over time">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="start"
            tickFormatter={(v: string) => format(new Date(v), tick)}
            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
            minTickGap={32}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
            tickFormatter={(v: number) => formatNumber(v)}
          />
          <Tooltip
            labelFormatter={(v) =>
              typeof v === 'string' ? format(new Date(v), 'MMM d, HH:mm') : ''
            }
            contentStyle={{
              background: 'var(--popover)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              fontSize: 12,
            }}
          />
          <Area
            type="monotone"
            dataKey="requests"
            name="Requests"
            stroke="var(--chart-1)"
            fill="var(--chart-1)"
            fillOpacity={0.15}
          />
          <Area
            type="monotone"
            dataKey="errors"
            name="Errors"
            stroke="var(--chart-3)"
            fill="var(--chart-3)"
            fillOpacity={0.15}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export function RangeSelect({
  value,
  onChange,
}: {
  value: RangeKey
  onChange: (value: RangeKey) => void
}) {
  return (
    <Select
      value={value}
      onValueChange={(v) => {
        onChange(v as RangeKey)
      }}
    >
      <SelectTrigger className="w-40" aria-label="Period">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(RANGES) as RangeKey[]).map((key) => (
          <SelectItem key={key} value={key}>
            {RANGES[key].label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
