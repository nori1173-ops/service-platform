import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createAppApi } from '@/services/http'
import { adminUsersApi } from '../api'

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>()
  return { ...actual, createAppApi: vi.fn(actual.createAppApi) }
})

const APP_ID = 'admin-users'
const USER_ID = '01JBEXAMPLE0000000000000002'

// アプリの axios インスタンスは api.ts の読み込み時に 1 つだけ作られる
const api = vi.mocked(createAppApi).mock.results[0].value

let captured: InternalAxiosRequestConfig[] = []
let responseData: unknown = null

beforeEach(() => {
  captured = []
  responseData = { ok: 1 }
  vi.mocked(fetchAuthSession).mockResolvedValue({} as Awaited<ReturnType<typeof fetchAuthSession>>)
  const adapter: AxiosAdapter = async (config) => {
    captured.push(config)
    return {
      data: { ok: true, data: responseData, error: null, meta: null },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } as AxiosResponse
  }
  api.defaults.adapter = adapter
})

function sent(): { method?: string; url?: string; body: unknown } {
  const [config] = captured
  return {
    method: config.method,
    url: config.url,
    body: config.data === undefined ? undefined : JSON.parse(String(config.data)),
  }
}

describe('adminUsersApi', () => {
  it('baseURL が /api/admin-users', () => {
    expect(createAppApi).toHaveBeenCalledWith(APP_ID)
    expect(api.defaults.baseURL).toBe(`/api/${APP_ID}`)
  })

  it('listUsers が GET /users の data を返す', async () => {
    responseData = { users: [], self_user_id: 'me' }

    expect(await adminUsersApi.listUsers()).toEqual({ users: [], self_user_id: 'me' })
    expect(sent()).toMatchObject({ method: 'get', url: '/users' })
  })

  it('getUser が GET /users/{user_id}', async () => {
    await adminUsersApi.getUser(USER_ID)

    expect(sent()).toMatchObject({ method: 'get', url: `/users/${USER_ID}` })
  })

  it('invite が POST /users に本文を送る', async () => {
    const input = {
      email: 'new@example.com',
      kind: 'customer' as const,
      role: 'user' as const,
      org_name: '株式会社サンプル',
      contact: '代表 03-0000-0000',
      note: 'メモ',
    }

    await adminUsersApi.invite(input)

    expect(sent()).toEqual({ method: 'post', url: '/users', body: input })
  })

  it.each([
    ['updateProfile', () => adminUsersApi.updateProfile(USER_ID, { note: 'x' }), 'put', '/profile', { note: 'x' }],
    ['changeEmail', () => adminUsersApi.changeEmail(USER_ID, 'b@example.com'), 'put', '/email', { email: 'b@example.com' }],
    ['suspend', () => adminUsersApi.suspend(USER_ID, '盗難の連絡'), 'post', '/suspend', { reason: '盗難の連絡' }],
    [
      'restore',
      () => adminUsersApi.restore(USER_ID, { verified_method: 'phone', note: '窓口に確認' }),
      'post',
      '/restore',
      { verified_method: 'phone', note: '窓口に確認' },
    ],
    [
      'reinvite',
      () =>
        adminUsersApi.reinvite(USER_ID, {
          reason: '端末の盗難',
          email: 'new@example.com',
          verified_before: { method: 'phone', note: '本人に確認' },
        }),
      'post',
      '/reinvite',
      {
        reason: '端末の盗難',
        email: 'new@example.com',
        verified_before: { method: 'phone', note: '本人に確認' },
      },
    ],
    [
      'activateAdmin',
      () => adminUsersApi.activateAdmin(USER_ID, { method: 'in_person', note: '対面' }),
      'post',
      '/admin-activation',
      { method: 'in_person', note: '対面' },
    ],
    ['revokeAdmin', () => adminUsersApi.revokeAdmin(USER_ID), 'put', '/admin', { admin: false }],
  ])('%s の経路と本文', async (_name, call, method, suffix, body) => {
    await call()

    expect(sent()).toEqual({ method, url: `/users/${USER_ID}${suffix}`, body })
  })

  it('issueTemporaryPassword が POST /users/{user_id}/temporary-password', async () => {
    responseData = { user_id: USER_ID, temporary_password: 'Xyz' }

    const result = await adminUsersApi.issueTemporaryPassword(USER_ID)

    expect(sent()).toMatchObject({ method: 'post', url: `/users/${USER_ID}/temporary-password` })
    expect(result.temporary_password).toBe('Xyz')
  })

  // user_id は URL に埋めるためエスケープする（想定外の値でも別のパスへ飛ばさない）
  it('user_id をパーセントエンコードする', async () => {
    await adminUsersApi.getUser('a/b c')

    expect(captured[0].url).toBe('/users/a%2Fb%20c')
  })

  it('data の無い応答は例外にする', async () => {
    responseData = null

    await expect(adminUsersApi.getUser(USER_ID)).rejects.toThrow()
  })
})

describe('利用期間APIの経路と本文', () => {
  const pid = 'period/id'
  const root = '/device-periods/period%2Fid'
  it('一覧のstatusを渡す', async () => {
    await adminUsersApi.listDevicePeriods('all')
    expect(sent()).toMatchObject({ method: 'get', url: '/device-periods?status=all' })
  })
  it.each([
    [() => adminUsersApi.getDevicePeriod(pid), 'get', root, undefined],
    [() => adminUsersApi.startDevicePeriod({ account: '00000001', org_name: '組織A', access: 'everyone' }), 'post', '/device-periods', { account: '00000001', org_name: '組織A', access: 'everyone' }],
    [() => adminUsersApi.setAccess(pid, 'assigned'), 'put', `${root}/access`, { access: 'assigned' }],
    [() => adminUsersApi.createGroup({ name: '会社A', note: null }), 'post', '/groups', { name: '会社A', note: null }],
    [() => adminUsersApi.addMember('g/1', 'u/1'), 'post', '/groups/g%2F1/members', { user_id: 'u/1' }],
    [() => adminUsersApi.removeMember('g/1', 'u/1'), 'post', '/groups/g%2F1/members/u%2F1/remove', undefined],
    [() => adminUsersApi.endDevicePeriod(pid), 'post', `${root}/end`, undefined],
    [() => adminUsersApi.assign(pid, { target_type: 'user', user_id: 'user', valid_from: '2026-11-04T10:00:00+09:00', valid_to: null }), 'post', `${root}/assignments`, { target_type: 'user', user_id: 'user', valid_from: '2026-11-04T10:00:00+09:00', valid_to: null }],
    [() => adminUsersApi.endAssignment(pid, 'a/id'), 'post', `${root}/assignments/a%2Fid/end`, undefined],
    [() => adminUsersApi.grant(pid, { service: 'camera', starts_at: '2026-11-04T10:00:00+09:00', ends_at: null }), 'post', `${root}/grants`, { service: 'camera', starts_at: '2026-11-04T10:00:00+09:00', ends_at: null }],
    [() => adminUsersApi.endGrant(pid, 'g/id'), 'post', `${root}/grants/g%2Fid/end`, undefined],
  ])('%s', async (call, method, url, body) => {
    await call()
    expect(sent()).toEqual({ method, url, body })
  })
})
