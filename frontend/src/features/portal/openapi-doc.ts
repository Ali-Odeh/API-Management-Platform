/**
 * Reads an OpenAPI 3 (or Swagger 2) document into what the docs page
 * shows. Documents come from workspaces and aren't validated beyond their
 * shape (PLAN.md, APIs), so every field is read defensively.
 */

export const DOC_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'] as const

export interface DocParameter {
  name: string
  in: string
  required: boolean
  description: string
  type: string
}

export interface DocOperation {
  method: string
  path: string
  summary: string
  description: string
  parameters: DocParameter[]
  /** The JSON body's schema, `$ref`s resolved a few levels deep. */
  body: unknown
  responses: { status: string; description: string }[]
}

export interface DocSummary {
  title: string
  version: string
  description: string
  operations: DocOperation[]
}

type Json = Record<string, unknown>

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** Follows `#/...` references inside the document, at most `depth` levels down. */
export function resolveRefs(value: unknown, doc: Json, depth = 4): unknown {
  if (Array.isArray(value)) return value.map((item) => resolveRefs(item, doc, depth))
  if (!isObject(value)) return value
  const ref = text(value.$ref)
  if (ref.startsWith('#/')) {
    if (depth === 0) return { $ref: ref }
    let target: unknown = doc
    for (const part of ref.slice(2).split('/')) target = isObject(target) ? target[part] : undefined
    return target === undefined ? { $ref: ref } : resolveRefs(target, doc, depth - 1)
  }
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveRefs(v, doc, depth)]))
}

function parameterType(param: Json): string {
  const schema = isObject(param.schema) ? param.schema : param
  return text(schema.type) || text(schema.format) || '—'
}

function readParameters(raw: unknown, doc: Json): DocParameter[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((p) => resolveRefs(p, doc, 2))
    .filter(isObject)
    .filter((p) => p.in !== 'body')
    .map((p) => ({
      name: text(p.name),
      in: text(p.in),
      required: p.required === true,
      description: text(p.description),
      type: parameterType(p),
    }))
}

function readBody(operation: Json, doc: Json): unknown {
  const body = resolveRefs(operation.requestBody, doc, 1)
  if (isObject(body) && isObject(body.content)) {
    const json = body.content['application/json'] ?? Object.values(body.content)[0]
    if (isObject(json) && json.schema !== undefined) return resolveRefs(json.schema, doc)
  }
  // Swagger 2.0: an `in: body` parameter carries the schema.
  const legacy = Array.isArray(operation.parameters)
    ? (operation.parameters as unknown[]).find((p) => isObject(p) && p.in === 'body')
    : undefined
  return isObject(legacy) ? resolveRefs(legacy.schema, doc) : undefined
}

/** Title, version, description and every operation, in the document's path order. */
export function summarizeDoc(doc: Json): DocSummary {
  const info = isObject(doc.info) ? doc.info : {}
  const paths = isObject(doc.paths) ? doc.paths : {}
  const operations: DocOperation[] = []
  for (const [path, item] of Object.entries(paths)) {
    if (!isObject(item)) continue
    const shared = readParameters(item.parameters, doc)
    for (const method of DOC_METHODS) {
      const op = item[method]
      if (!isObject(op)) continue
      const responses = isObject(op.responses) ? op.responses : {}
      operations.push({
        method: method.toUpperCase(),
        path,
        summary: text(op.summary),
        description: text(op.description),
        parameters: [...shared, ...readParameters(op.parameters, doc)],
        body: readBody(op, doc),
        responses: Object.entries(responses).map(([status, r]) => ({
          status,
          description: isObject(r) ? text(r.description) : '',
        })),
      })
    }
  }
  return {
    title: text(info.title) || 'Untitled API',
    version: text(info.version),
    description: text(info.description),
    operations,
  }
}
