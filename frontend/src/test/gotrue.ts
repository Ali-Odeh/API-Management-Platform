import { http, HttpResponse } from 'msw'

/**
 * A small in-memory GoTrue for tests: password sign-in, refresh, the user,
 * TOTP factors (any code "123456" is right), recovery and sign-out. Tokens
 * are unsigned JWTs: GoTrueClient only reads their payload.
 */
interface FakeFactor {
  id: string
  friendly_name: string
  factor_type: 'totp'
  status: 'verified' | 'unverified'
  created_at: string
  updated_at: string
}

interface FakeUser {
  id: string
  email: string
  password: string
  banned: boolean
  factors: FakeFactor[]
}

export const VALID_CODE = '123456'
const NOW = '2026-11-23T10:00:00Z'

function base64url(value: object): string {
  return btoa(JSON.stringify(value)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')
}

export function fakeJwt(claims: Record<string, unknown>): string {
  const exp = Math.floor(Date.now() / 1000) + 3600
  return `${base64url({ alg: 'HS256', typ: 'JWT' })}.${base64url({ exp, ...claims })}.sig`
}

export class FakeGoTrue {
  readonly users = new Map<string, FakeUser>()
  /** Sessions by access token, with their assurance level. */
  private readonly tokens = new Map<string, { userId: string; aal: 'aal1' | 'aal2' }>()
  readonly calls: string[] = []
  private counter = 0

  constructor(readonly base: string) {}

  addUser(email: string, password: string, options: { mfa?: boolean; banned?: boolean } = {}) {
    const id = `00000000-0000-4000-8000-${String(++this.counter).padStart(12, '0')}`
    const factors: FakeFactor[] = options.mfa
      ? [
          {
            id: `factor-${id}`,
            friendly_name: 'Phone',
            factor_type: 'totp',
            status: 'verified',
            created_at: NOW,
            updated_at: NOW,
          },
        ]
      : []
    const user = { id, email, password, banned: options.banned ?? false, factors }
    this.users.set(id, user)
    return user
  }

  /** A stored session, as if the person signed in earlier (written where GoTrueClient reads it). */
  storeSession(storageKey: string, email: string, aal: 'aal1' | 'aal2' = 'aal1') {
    const user = this.byEmail(email)
    if (!user) throw new Error(`no fake user ${email}`)
    localStorage.setItem(storageKey, JSON.stringify(this.issue(user, aal)))
  }

  private byEmail(email: string) {
    return [...this.users.values()].find((u) => u.email === email)
  }

  private publicUser(user: FakeUser) {
    return {
      id: user.id,
      aud: 'authenticated',
      role: 'authenticated',
      email: user.email,
      app_metadata: {},
      user_metadata: {},
      created_at: NOW,
      factors: user.factors.length ? user.factors : undefined,
    }
  }

  private issue(user: FakeUser, aal: 'aal1' | 'aal2') {
    const access_token = fakeJwt({
      sub: user.id,
      email: user.email,
      aud: 'authenticated',
      aal,
      amr: [{ method: aal === 'aal2' ? 'totp' : 'password', timestamp: 1 }],
      session_id: `s-${++this.counter}`,
    })
    this.tokens.set(access_token, { userId: user.id, aal })
    return {
      access_token,
      refresh_token: `refresh-${user.id}-${aal}-${this.counter}`,
      token_type: 'bearer',
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      user: this.publicUser(user),
    }
  }

  private sessionOf(request: Request) {
    const token = request.headers.get('Authorization')?.replace(/^Bearer /, '') ?? ''
    const session = this.tokens.get(token)
    const user = session && this.users.get(session.userId)
    return session && user ? { ...session, user } : null
  }

  private error(status: number, code: string, msg: string) {
    return HttpResponse.json({ code: status, error_code: code, msg }, { status })
  }

  handlers() {
    const url = (path: string) => `${this.base}${path}`
    const log = (name: string) => {
      this.calls.push(name)
    }
    return [
      http.post(url('/token'), async ({ request }) => {
        const grant = new URL(request.url).searchParams.get('grant_type')
        const body = (await request.json()) as Record<string, string>
        log(`token:${grant ?? ''}`)
        if (grant === 'refresh_token') {
          const [, id, aal] = /^refresh-(.+)-(aal\d)-\d+$/.exec(body.refresh_token ?? '') ?? []
          const user = id ? this.users.get(id) : undefined
          if (!user) return this.error(400, 'refresh_token_not_found', 'Invalid Refresh Token')
          return HttpResponse.json(this.issue(user, aal === 'aal2' ? 'aal2' : 'aal1'))
        }
        const user = this.byEmail(body.email ?? '')
        if (!user || user.password !== body.password) {
          return this.error(400, 'invalid_credentials', 'Invalid login credentials')
        }
        if (user.banned) return this.error(400, 'user_banned', 'User is banned')
        return HttpResponse.json(this.issue(user, 'aal1'))
      }),
      http.get(url('/user'), ({ request }) => {
        const session = this.sessionOf(request)
        if (!session) return this.error(401, 'bad_jwt', 'invalid JWT')
        return HttpResponse.json(this.publicUser(session.user))
      }),
      http.put(url('/user'), async ({ request }) => {
        const session = this.sessionOf(request)
        if (!session) return this.error(401, 'bad_jwt', 'invalid JWT')
        const body = (await request.json()) as { password?: string }
        log('user:update')
        if (session.user.factors.some((f) => f.status === 'verified') && session.aal !== 'aal2') {
          return this.error(401, 'insufficient_aal', 'AAL2 required')
        }
        if (body.password === session.user.password) {
          return this.error(422, 'same_password', 'New password should be different')
        }
        if (body.password) session.user.password = body.password
        return HttpResponse.json(this.publicUser(session.user))
      }),
      http.post(url('/logout'), ({ request }) => {
        log(`logout:${new URL(request.url).searchParams.get('scope') ?? 'global'}`)
        return new HttpResponse(null, { status: 204 })
      }),
      http.post(url('/recover'), async ({ request }) => {
        const body = (await request.json()) as { email: string }
        log(`recover:${body.email}`)
        return HttpResponse.json({})
      }),
      http.post(url('/factors'), async ({ request }) => {
        const session = this.sessionOf(request)
        if (!session) return this.error(401, 'bad_jwt', 'invalid JWT')
        const body = (await request.json()) as { friendly_name: string }
        if (session.user.factors.some((f) => f.friendly_name === body.friendly_name)) {
          return this.error(422, 'mfa_factor_name_conflict', 'A factor with this name exists')
        }
        const factor: FakeFactor = {
          id: `factor-${++this.counter}`,
          friendly_name: body.friendly_name,
          factor_type: 'totp',
          status: 'unverified',
          created_at: NOW,
          updated_at: NOW,
        }
        session.user.factors.push(factor)
        return HttpResponse.json({
          id: factor.id,
          type: 'totp',
          friendly_name: factor.friendly_name,
          totp: {
            qr_code: 'data:image/svg+xml;utf-8,<svg/>',
            secret: 'JBSWY3DPEHPK3PXP',
            uri: 'otpauth://x',
          },
        })
      }),
      http.post(url('/factors/:id/challenge'), ({ params }) =>
        HttpResponse.json({
          id: `challenge-${String(params.id)}`,
          type: 'totp',
          expires_at: 9999999999,
        }),
      ),
      http.post(url('/factors/:id/verify'), async ({ request, params }) => {
        const session = this.sessionOf(request)
        if (!session) return this.error(401, 'bad_jwt', 'invalid JWT')
        const body = (await request.json()) as { code: string }
        log('factor:verify')
        log(`factor:verify:${String(params.id)}`)
        if (body.code !== VALID_CODE) {
          return this.error(422, 'mfa_verification_failed', 'Invalid TOTP code entered')
        }
        const factor = session.user.factors.find((f) => f.id === params.id)
        if (factor) factor.status = 'verified'
        return HttpResponse.json(this.issue(session.user, 'aal2'))
      }),
      http.delete(url('/factors/:id'), ({ request, params }) => {
        const session = this.sessionOf(request)
        if (!session) return this.error(401, 'bad_jwt', 'invalid JWT')
        const factor = session.user.factors.find((f) => f.id === params.id)
        if (factor?.status === 'verified' && session.aal !== 'aal2') {
          return this.error(401, 'insufficient_aal', 'AAL2 required')
        }
        session.user.factors = session.user.factors.filter((f) => f.id !== params.id)
        return HttpResponse.json({ id: params.id })
      }),
    ]
  }
}
