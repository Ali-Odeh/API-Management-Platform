import { Waypoints } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Logo({
  className,
  label = 'API Platform',
}: {
  className?: string
  label?: string
}) {
  return (
    <span className={cn('flex items-center gap-2 font-semibold', className)}>
      <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md">
        <Waypoints aria-hidden className="size-4" />
      </span>
      {label}
    </span>
  )
}
