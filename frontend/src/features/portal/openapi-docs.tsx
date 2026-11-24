import { Badge } from '@/components/ui/badge'
import { JsonBlock } from '@/components/details'
import { cn } from '@/lib/utils'
import type { DocOperation, DocSummary } from './openapi-doc'

const METHOD_CLASS: Record<string, string> = {
  GET: 'bg-chart-1/15 text-chart-1',
  POST: 'bg-chart-4/15 text-chart-4',
  PUT: 'bg-chart-2/20 text-foreground',
  PATCH: 'bg-chart-2/20 text-foreground',
  DELETE: 'bg-chart-3/15 text-chart-3',
}

export function MethodBadge({ method }: { method: string }) {
  return (
    <Badge
      variant="secondary"
      className={cn('w-16 justify-center font-mono', METHOD_CLASS[method])}
    >
      {method}
    </Badge>
  )
}

/** An API's OpenAPI document as a page: each operation with parameters, body and responses. */
export function OpenApiDocs({
  doc,
  onTry,
}: {
  doc: DocSummary
  /** Opens the try-it console on this operation. */
  onTry: (operation: DocOperation) => void
}) {
  if (doc.operations.length === 0) {
    return <p className="text-muted-foreground text-sm">The document lists no operations.</p>
  }
  return (
    <div className="grid gap-3">
      {doc.operations.map((op) => (
        <details key={`${op.method} ${op.path}`} className="group rounded-md border">
          <summary className="flex cursor-pointer list-none items-center gap-3 p-3">
            <MethodBadge method={op.method} />
            <code className="font-mono text-sm break-all">{op.path}</code>
            <span className="text-muted-foreground truncate text-sm">{op.summary}</span>
          </summary>
          <div className="grid gap-4 border-t p-3 text-sm">
            {op.description && <p className="whitespace-pre-wrap">{op.description}</p>}
            {op.parameters.length > 0 && (
              <div className="grid gap-1">
                <p className="font-medium">Parameters</p>
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Name</th>
                      <th className="py-1 pr-3 font-medium">In</th>
                      <th className="py-1 pr-3 font-medium">Type</th>
                      <th className="py-1 font-medium">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {op.parameters.map((p) => (
                      <tr key={`${p.in}:${p.name}`} className="border-t">
                        <td className="py-1 pr-3 font-mono">
                          {p.name}
                          {p.required && <span className="text-destructive"> *</span>}
                        </td>
                        <td className="py-1 pr-3">{p.in}</td>
                        <td className="py-1 pr-3">{p.type}</td>
                        <td className="py-1">{p.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {op.body !== undefined && (
              <div className="grid gap-1">
                <p className="font-medium">Request body</p>
                <JsonBlock value={op.body} />
              </div>
            )}
            {op.responses.length > 0 && (
              <div className="grid gap-1">
                <p className="font-medium">Responses</p>
                <ul className="grid gap-1">
                  {op.responses.map((r) => (
                    <li key={r.status} className="flex gap-2">
                      <code className="font-mono">{r.status}</code>
                      <span className="text-muted-foreground">{r.description}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div>
              <button
                type="button"
                className="text-sm font-medium underline-offset-4 hover:underline"
                onClick={() => {
                  onTry(op)
                }}
              >
                Try it →
              </button>
            </div>
          </div>
        </details>
      ))}
    </div>
  )
}
