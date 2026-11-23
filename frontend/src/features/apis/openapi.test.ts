import { describe, expect, it } from 'vitest'
import { describeOpenApi, parseOpenApi } from './openapi'

const DOC = {
  openapi: '3.1.0',
  info: { title: 'Orders', version: '2.0' },
  paths: { '/a': {}, '/b': {} },
}

describe('OpenAPI documents', () => {
  it('accepts JSON with openapi or swagger 2.0 and an info object', () => {
    expect(parseOpenApi(JSON.stringify(DOC))).toEqual({ doc: DOC })
    expect(parseOpenApi('{"swagger": "2.0", "info": {}}')).toHaveProperty('doc')
    expect(parseOpenApi('openapi: 3.1.0')).toEqual({
      error: expect.stringMatching(/not valid JSON/) as string,
    })
    expect(parseOpenApi('[1]')).toEqual({ error: 'The document must be a JSON object.' })
    expect(parseOpenApi('{"info": {}}')).toEqual({
      error: expect.stringMatching(/"openapi"/) as string,
    })
    expect(parseOpenApi('{"openapi": "3.0.0"}')).toEqual({ error: 'It needs an "info" object.' })
  })

  it('summarizes a document', () => {
    expect(describeOpenApi(DOC)).toEqual({ title: 'Orders', version: '2.0', paths: 2 })
    expect(describeOpenApi({ openapi: '3.0.0', info: { title: 3 } })).toEqual({
      title: null,
      version: null,
      paths: 0,
    })
    expect(describeOpenApi({})).toEqual({ title: null, version: null, paths: 0 })
  })
})
