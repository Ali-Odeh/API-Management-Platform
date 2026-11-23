import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { memberAuth } from '@/lib/auth/gotrue'
import { api as url, detail, json, MEMBER_STORAGE_KEY, memberGoTrue, server } from '@/test/server'
import { api, fetchAll, unwrap } from './client'
import { ApiError } from './errors'

function signedIn() {
  memberGoTrue.addUser('owner@example.test', 'pw')
  memberGoTrue.storeSession(MEMBER_STORAGE_KEY, 'owner@example.test')
}

describe('api client', () => {
  it('sends the access token and returns typed data', async () => {
    signedIn()
    let auth: string | null = null
    server.use(
      http.get(url('/api/v1/me'), ({ request }) => {
        auth = request.headers.get('Authorization')
        return json({
          id: 'u',
          email: 'owner@example.test',
          is_active: true,
          is_platform_admin: false,
          created_at: 'x',
        })
      }),
    )
    const me = await unwrap(api.GET('/api/v1/me'))
    expect(me.email).toBe('owner@example.test')
    expect(auth).toMatch(/^Bearer ey/)
  })

  it('throws ApiError with the detail', async () => {
    server.use(http.get(url('/api/v1/me'), () => detail(403, 'permission_denied')))
    await expect(unwrap(api.GET('/api/v1/me'))).rejects.toMatchObject({
      status: 403,
      detail: 'permission_denied',
    })
  })

  it('signs the person out of this browser on 401', async () => {
    signedIn()
    server.use(http.get(url('/api/v1/me'), () => detail(401, 'Not authenticated')))
    await expect(unwrap(api.GET('/api/v1/me'))).rejects.toBeInstanceOf(ApiError)
    const { data } = await memberAuth.getSession()
    expect(data.session).toBeNull()
  })

  it('signs a disabled person out, but not on other 403s', async () => {
    signedIn()
    server.use(http.get(url('/api/v1/me'), () => detail(403, 'permission_denied')))
    await expect(unwrap(api.GET('/api/v1/me'))).rejects.toBeInstanceOf(ApiError)
    expect((await memberAuth.getSession()).data.session).not.toBeNull()

    server.use(http.get(url('/api/v1/me'), () => detail(403, 'user_disabled')))
    await expect(unwrap(api.GET('/api/v1/me'))).rejects.toBeInstanceOf(ApiError)
    expect((await memberAuth.getSession()).data.session).toBeNull()
  })

  it('reports a network failure as status 0', async () => {
    server.use(http.get(url('/api/v1/me'), () => HttpResponse.error()))
    await expect(unwrap(api.GET('/api/v1/me'))).rejects.toMatchObject({ status: 0 })
  })

  it('reads every page of a list', async () => {
    const pages: number[] = []
    server.use(
      http.get(url('/api/v1/me/workspaces'), ({ request }) => {
        const n = Number(new URL(request.url).searchParams.get('page'))
        pages.push(n)
        return json({ items: n < 3 ? [n, n] : [n], total: 5 })
      }),
    )
    const items = await fetchAll((page) =>
      api.GET('/api/v1/me/workspaces', { params: { query: { page, page_size: 2 } } }),
    )
    expect(items).toEqual([1, 1, 2, 2, 3])
    expect(pages).toEqual([1, 2, 3])
  })
})
