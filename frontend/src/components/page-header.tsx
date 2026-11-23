import { cn } from '@/lib/utils'

/** A page's title (level 1) or a section's within a page (level 2), with its actions. */
export function PageHeader({
  title,
  description,
  actions,
  level = 1,
}: {
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  level?: 1 | 2
}) {
  const Heading = level === 1 ? 'h1' : 'h2'
  return (
    <div
      className={cn(
        'flex flex-wrap items-start justify-between gap-4',
        level === 1 ? 'mb-6' : 'mb-3',
      )}
    >
      <div className="min-w-0">
        <Heading
          className={cn('font-semibold tracking-tight', level === 1 ? 'text-2xl' : 'text-lg')}
        >
          {title}
        </Heading>
        {description && <p className="text-muted-foreground mt-1 text-sm">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
