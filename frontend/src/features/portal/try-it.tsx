import { Send } from 'lucide-react'
import { useState } from 'react'
import { CopyButton } from '@/components/copy-button'
import { Spinner } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { HTTP_METHODS, statusClass } from '@/lib/http'
import { formatMs } from '@/lib/metrics'
import { cn } from '@/lib/utils'
import { curlCommand } from './gateway'

export interface TryRequest {
  method: string
  path: string
}

interface TryResponse {
  status: number
  ms: number
  contentType: string
  body: string
}

function pretty(contentType: string, text: string): string {
  if (!contentType.includes('json')) return text
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

const WITH_BODY = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/**
 * Calls the API through the gateway, from the browser, with the
 * developer's key: `/try/<slug>/...` on the app's own origin, which the dev
 * server forwards to the workspace's gateway host.
 */
export function TryIt({ slug, request }: { slug: string; request: TryRequest }) {
  const [method, setMethod] = useState(request.method)
  const [path, setPath] = useState(request.path)
  const [key, setKey] = useState('')
  const [body, setBody] = useState('')
  const [pending, setPending] = useState(false)
  const [response, setResponse] = useState<TryResponse | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function send() {
    setPending(true)
    setFailure(null)
    const started = performance.now()
    try {
      const headers: Record<string, string> = {}
      if (key.trim()) headers['X-API-Key'] = key.trim()
      const hasBody = WITH_BODY.has(method) && body.trim() !== ''
      if (hasBody) headers['Content-Type'] = 'application/json'
      const answer = await fetch(`/try/${slug}${path.startsWith('/') ? path : `/${path}`}`, {
        method,
        headers,
        body: hasBody ? body : undefined,
      })
      const contentType = answer.headers.get('content-type') ?? ''
      setResponse({
        status: answer.status,
        ms: performance.now() - started,
        contentType,
        body: pretty(contentType, await answer.text()),
      })
    } catch {
      setResponse(null)
      setFailure('The gateway could not be reached.')
    } finally {
      setPending(false)
    }
  }

  return (
    <Card id="try-it">
      <CardHeader>
        <CardTitle>Try it</CardTitle>
        <CardDescription>
          Sends a real request through the gateway. Paste a key of one of your approved apps for
          routes that need one.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <div className="flex gap-2">
            <Select value={method} onValueChange={setMethod}>
              <SelectTrigger className="w-28 font-mono" aria-label="Method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HTTP_METHODS.map((m) => (
                  <SelectItem key={m} value={m} className="font-mono">
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              aria-label="Path"
              className="font-mono"
              value={path}
              onChange={(e) => {
                setPath(e.target.value)
              }}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="try-key">API key</Label>
            <Input
              id="try-key"
              type="password"
              autoComplete="off"
              className="font-mono"
              placeholder="ak_live_…"
              value={key}
              onChange={(e) => {
                setKey(e.target.value)
              }}
            />
          </div>
          {WITH_BODY.has(method) && (
            <div className="grid gap-2">
              <Label htmlFor="try-body">JSON body</Label>
              <Textarea
                id="try-body"
                rows={4}
                className="font-mono text-xs"
                value={body}
                onChange={(e) => {
                  setBody(e.target.value)
                }}
              />
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={pending}>
              {pending ? <Spinner /> : <Send />} Send
            </Button>
            <CopyButton value={curlCommand(slug, method, path, key, body)} label="Copy as curl" />
          </div>
        </form>
        {failure && (
          <p role="alert" className="text-destructive text-sm">
            {failure}
          </p>
        )}
        {response && (
          <div className="grid gap-2" aria-live="polite">
            <p className="flex items-center gap-3 text-sm">
              <span className={cn('font-mono font-semibold', statusClass(response.status))}>
                {response.status}
              </span>
              <span className="text-muted-foreground">{formatMs(response.ms)}</span>
            </p>
            <pre
              aria-label="Response body"
              className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs"
            >
              {response.body || '(empty)'}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
