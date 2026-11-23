import { describe, expect, it } from 'vitest'
import { ApiError, describeCode, errorMessage } from '@/api/errors'
import { shouldRetry } from '@/app/query-client'
import { authErrorMessage } from './auth/errors'
import { needsNewPassword, parseAuthRedirect } from './auth/redirect'
import { formatDate, formatDateTime, formatNumber, formatRelative, safeNextPath } from './format'
import { decide, type Access } from './permissions'
import { AuthApiError, AuthRetryableFetchError } from '@supabase/auth-js'

describe('ApiError', () => {
  it('keeps a string detail', () => {
    const error = ApiError.fromBody(409, { detail: 'quota_exceeded' })
    expect(error.status).toBe(409)
    expect(error.detail).toBe('quota_exceeded')
    expect(errorMessage(error)).toBe('The workspace has reached its limit for this.')
  })

  it('turns validation errors into field messages', () => {
    const error = ApiError.fromBody(422, {
      detail: [
        { loc: ['body', 'targets', 0, 'url'], msg: 'Value error, internal address', type: 'x' },
        { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
        { loc: ['body', 'name'], msg: 'Second message', type: 'x' },
        { msg: 'Whole body is wrong', type: 'x' },
      ],
    })
    expect(error.fields).toEqual({ 'targets.0.url': 'internal address', name: 'Field required' })
    expect(error.detail).toContain('targets.0.url: internal address')
    expect(error.detail).toContain('Whole body is wrong')
  })

  it('has a message for bodies without a detail', () => {
    expect(ApiError.fromBody(500, null).detail).toMatch(/server/)
    expect(ApiError.fromBody(418, 'teapot').detail).toBe('Request failed (418)')
    expect(ApiError.fromBody(0, undefined).detail).toBe('The server could not be reached')
    expect(ApiError.fromBody(422, { detail: [] }).detail).toBe('Request failed (422)')
  })

  it('describes status codes of workspaces and APIs', () => {
    expect(describeCode('workspace_deleted')).toBe('The workspace is deleted.')
    expect(describeCode('api_draft')).toBe('The API is draft.')
    expect(describeCode('Route not found')).toBe('Route not found')
    expect(errorMessage(new Error('plain'))).toBe('plain')
    expect(errorMessage('weird')).toBe('Something went wrong')
  })

  it('never retries a refused request', () => {
    expect(shouldRetry(0, new ApiError(404, 'x'))).toBe(false)
    expect(shouldRetry(0, new ApiError(503, 'x'))).toBe(true)
    expect(shouldRetry(2, new Error('network'))).toBe(false)
  })
})

describe('auth errors', () => {
  it('uses the code when GoTrue gives one', () => {
    expect(authErrorMessage(new AuthApiError('x', 400, 'invalid_credentials'))).toBe(
      'Wrong email or password.',
    )
    expect(authErrorMessage(new AuthApiError('raw message', 400, 'unknown_code'))).toBe(
      'raw message',
    )
    expect(authErrorMessage(new AuthRetryableFetchError('fetch failed', 0))).toBe(
      'The sign-in server could not be reached.',
    )
    expect(authErrorMessage(new Error('boom'))).toBe('boom')
    expect(authErrorMessage(42)).toBe('Something went wrong')
  })
})

describe('auth redirect', () => {
  it('reads a session, an error, or nothing from the fragment', () => {
    expect(parseAuthRedirect('#access_token=a&refresh_token=r&type=recovery')).toEqual({
      kind: 'session',
      accessToken: 'a',
      refreshToken: 'r',
      type: 'recovery',
    })
    expect(parseAuthRedirect('#access_token=a&refresh_token=r')).toMatchObject({ type: '' })
    expect(parseAuthRedirect('#error=access_denied')).toEqual({
      kind: 'error',
      message: 'access_denied',
    })
    expect(parseAuthRedirect('#section-2')).toBeNull()
    expect(parseAuthRedirect('')).toBeNull()
  })

  it('asks for a password after invitations and recoveries only', () => {
    expect(needsNewPassword('invite')).toBe(true)
    expect(needsNewPassword('recovery')).toBe(true)
    expect(needsNewPassword('signup')).toBe(false)
  })
})

describe('format', () => {
  it('formats dates and numbers', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDate(undefined)).toBe('—')
    expect(formatDate('2026-11-23T12:00:00Z')).toBe('Nov 23, 2026')
    expect(formatDateTime('2026-11-23T12:00:00Z')).toMatch(/^Nov 23, 2026, \d\d:\d\d$/)
    expect(formatRelative(null)).toBe('never')
    expect(formatRelative(new Date(Date.now() - 120_000).toISOString())).toBe('2 minutes ago')
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatNumber(null)).toBe('—')
  })

  it('allows only paths inside the app after login', () => {
    expect(safeNextPath('/w/acme/routes?x=1')).toBe('/w/acme/routes?x=1')
    expect(safeNextPath(null)).toBe('/')
    expect(safeNextPath('https://evil.example')).toBe('/')
    expect(safeNextPath('//evil.example')).toBe('/')
    expect(safeNextPath('/\\evil.example')).toBe('/')
  })
})

describe('permissions', () => {
  const base: Access = {
    role: null,
    isPlatformAdmin: false,
    aal: 'aal1',
    workspaceStatus: 'active',
  }

  it.each([
    ['viewer', 'services:read', 'allowed'],
    ['viewer', 'services:write', 'denied'],
    ['viewer', 'audit:read', 'denied'],
    ['api_admin', 'services:write', 'allowed'],
    ['api_admin', 'audit:read', 'allowed'],
    ['api_admin', 'members:write', 'denied'],
    ['owner', 'members:write', 'allowed'],
    ['owner', 'workspace:delete', 'allowed'],
  ] as const)('%s may %s: %s', (role, permission, expected) => {
    expect(decide({ ...base, role }, permission)).toBe(expected)
  })

  it('makes a suspended workspace read-only for members', () => {
    const access: Access = { ...base, role: 'owner', workspaceStatus: 'suspended' }
    expect(decide(access, 'services:read')).toBe('allowed')
    expect(decide(access, 'services:write')).toBe('denied')
  })

  it('lets the Platform Admin do the rest with MFA only', () => {
    const admin: Access = { ...base, isPlatformAdmin: true }
    expect(decide(admin, 'services:write')).toBe('needs_mfa')
    expect(decide({ ...admin, aal: 'aal2' }, 'services:write')).toBe('allowed')
    expect(decide({ ...admin, role: 'viewer' }, 'services:read')).toBe('allowed')
    expect(decide({ ...admin, role: 'owner', workspaceStatus: 'suspended' }, 'routes:write')).toBe(
      'needs_mfa',
    )
  })
})
