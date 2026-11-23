function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/** A document's title, version and number of paths, for a summary line. */
export function describeOpenApi(doc: Record<string, unknown>): {
  title: string | null
  version: string | null
  paths: number
} {
  const info =
    typeof doc.info === 'object' && doc.info !== null ? (doc.info as Record<string, unknown>) : {}
  const paths =
    typeof doc.paths === 'object' && doc.paths !== null ? Object.keys(doc.paths).length : 0
  return { title: text(info.title), version: text(info.version), paths }
}

/** Reads text as an OpenAPI document the API accepts, or says why not. */
export function parseOpenApi(text: string): { doc: Record<string, unknown> } | { error: string } {
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return { error: 'This is not valid JSON. (YAML must be converted to JSON first.)' }
  }
  if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) {
    return { error: 'The document must be a JSON object.' }
  }
  const record = doc as Record<string, unknown>
  if (typeof record.openapi !== 'string' && record.swagger !== '2.0') {
    return { error: 'It needs an "openapi": "3.x" (or "swagger": "2.0") field.' }
  }
  if (typeof record.info !== 'object' || record.info === null) {
    return { error: 'It needs an "info" object.' }
  }
  return { doc: record }
}

/** A file's text (FileReader works everywhere, `Blob.text` not in every runtime). */
export function readFileText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve(typeof reader.result === 'string' ? reader.result : '')
    }
    reader.onerror = () => {
      reject(reader.error ?? new Error('The file could not be read'))
    }
    reader.readAsText(file)
  })
}
