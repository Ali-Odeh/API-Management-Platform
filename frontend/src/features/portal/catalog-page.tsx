import { FileText, Lock } from 'lucide-react'
import { Link } from 'react-router'
import { StatusBadge } from '@/components/badges'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState, LoadingState } from '@/components/states'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useCatalogApis, useCatalogWorkspaces, type CatalogWorkspace } from './queries'

/** `/portal`: every published API, by the workspace that offers it. */
export function CatalogPage() {
  const workspaces = useCatalogWorkspaces()
  return (
    <>
      <PageHeader
        title="APIs"
        description="Pick an API, read its docs, try it, and create an app to get a key."
      />
      {workspaces.isPending ? (
        <LoadingState />
      ) : workspaces.error ? (
        <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} />
      ) : workspaces.data.length === 0 ? (
        <EmptyState
          title="No APIs are published yet"
          description="Come back when a workspace publishes one."
        />
      ) : (
        <div className="grid gap-10">
          {workspaces.data.map((ws) => (
            <WorkspaceApis key={ws.slug} workspace={ws} />
          ))}
        </div>
      )}
    </>
  )
}

function WorkspaceApis({ workspace }: { workspace: CatalogWorkspace }) {
  const apis = useCatalogApis(workspace.slug)
  return (
    <section aria-labelledby={`ws-${workspace.slug}`}>
      <h2 id={`ws-${workspace.slug}`} className="mb-3 text-lg font-semibold">
        {workspace.name}
      </h2>
      {apis.isPending ? (
        <LoadingState />
      ) : apis.error ? (
        <ErrorState error={apis.error} onRetry={() => void apis.refetch()} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {apis.data.map((api) => (
            <Link
              key={api.id}
              to={`/portal/apis/${workspace.slug}/${api.id}`}
              className="focus-visible:ring-ring/50 rounded-xl outline-none focus-visible:ring-[3px]"
            >
              <Card className="hover:border-foreground/30 h-full transition-colors">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-2">
                    <span className="truncate">{api.name}</span>
                    {api.status === 'deprecated' && <StatusBadge status="deprecated" />}
                  </CardTitle>
                  <CardDescription className="line-clamp-3">
                    {api.description || 'No description.'}
                  </CardDescription>
                </CardHeader>
                <CardContent className="text-muted-foreground mt-auto flex flex-wrap gap-3 text-xs">
                  <span>{api.routes.length === 1 ? '1 route' : `${api.routes.length} routes`}</span>
                  {api.routes.some((r) => r.auth_required) && (
                    <span className="flex items-center gap-1">
                      <Lock aria-hidden className="size-3" /> API key
                    </span>
                  )}
                  {api.has_openapi && (
                    <span className="flex items-center gap-1">
                      <FileText aria-hidden className="size-3" /> Docs
                    </span>
                  )}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}
