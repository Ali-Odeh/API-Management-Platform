import { describe, expect, it } from 'vitest'
import { curlCommand, gatewayUrl } from './gateway'
import { resolveRefs, summarizeDoc } from './openapi-doc'

describe('reading an OpenAPI document', () => {
  it('lists operations with shared and own parameters, body and responses', () => {
    const doc = summarizeDoc({
      openapi: '3.1.0',
      info: { title: 'Shop', version: '1.0', description: 'All of it' },
      components: {
        schemas: {
          Order: { type: 'object', properties: { item: { $ref: '#/components/schemas/Item' } } },
          Item: { type: 'string' },
        },
      },
      paths: {
        '/orders/{id}': {
          parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'string' } }],
          get: { summary: 'Read', responses: { '200': { description: 'OK' } } },
          put: {
            parameters: [{ name: 'dry', in: 'query', type: 'boolean' }],
            requestBody: {
              content: { 'application/json': { schema: { $ref: '#/components/schemas/Order' } } },
            },
            responses: { '204': 'not an object' },
          },
          'x-extension': {},
        },
        '/broken': 'not an object',
      },
    })
    expect(doc.title).toBe('Shop')
    expect(doc.version).toBe('1.0')
    expect(doc.operations.map((o) => `${o.method} ${o.path}`)).toEqual([
      'GET /orders/{id}',
      'PUT /orders/{id}',
    ])
    const put = doc.operations[1]
    expect(put?.parameters).toEqual([
      { name: 'id', in: 'path', required: true, description: '', type: 'string' },
      { name: 'dry', in: 'query', required: false, description: '', type: 'boolean' },
    ])
    expect(put?.body).toEqual({ type: 'object', properties: { item: { type: 'string' } } })
    expect(put?.responses).toEqual([{ status: '204', description: '' }])
  })

  it('reads a Swagger 2.0 body parameter, and survives an empty document', () => {
    const doc = summarizeDoc({
      swagger: '2.0',
      paths: {
        '/items': {
          post: { parameters: [{ in: 'body', name: 'item', schema: { type: 'object' } }] },
        },
      },
    })
    expect(doc.title).toBe('Untitled API')
    expect(doc.operations[0]?.body).toEqual({ type: 'object' })
    expect(doc.operations[0]?.parameters).toEqual([])
    expect(summarizeDoc({}).operations).toEqual([])
  })

  it('stops following references that loop or point nowhere', () => {
    const doc = { components: { schemas: { A: { $ref: '#/components/schemas/A' } } } }
    expect(resolveRefs({ $ref: '#/components/schemas/A' }, doc, 2)).toEqual({
      $ref: '#/components/schemas/A',
    })
    expect(resolveRefs({ $ref: '#/nowhere' }, doc)).toEqual({ $ref: '#/nowhere' })
    expect(resolveRefs([1, 'two'], doc)).toEqual([1, 'two'])
  })
})

describe('gateway addresses', () => {
  it('writes the URL and a curl command', () => {
    expect(gatewayUrl('acme', '/v1/x')).toBe('http://acme.localhost:8081/v1/x')
    expect(curlCommand('acme', 'GET', '/v1/x', '', '')).toBe(
      'curl -X GET "http://acme.localhost:8081/v1/x" -H "X-API-Key: <your key>"',
    )
    expect(curlCommand('acme', 'POST', '/v1/x', 'ak_1', '{"a":1}')).toBe(
      'curl -X POST "http://acme.localhost:8081/v1/x" -H "X-API-Key: ak_1" -H "Content-Type: application/json" -d \'{"a":1}\'',
    )
  })
})
