/** The gateway's address for a workspace, as callers outside this page use it. */
export function gatewayUrl(slug: string, path: string): string {
  return `http://${slug}.localhost:8081${path}`
}

/** A curl command for the same call, with the key (or a placeholder). */
export function curlCommand(slug: string, method: string, path: string, key: string, body: string) {
  const parts = [`curl -X ${method} "${gatewayUrl(slug, path)}"`]
  parts.push(`-H "X-API-Key: ${key || '<your key>'}"`)
  if (body.trim()) parts.push(`-H "Content-Type: application/json" -d '${body.trim()}'`)
  return parts.join(' ')
}

/** Where GoTrue's portal emails (confirmation, recovery) lead back to. */
export function portalLinkTarget(): string {
  return `${window.location.origin}/portal`
}
