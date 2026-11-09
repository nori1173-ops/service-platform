import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createAppApi } from '@/services/http'
import { baseApi } from './baseApi'

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>()
  return { ...actual, createAppApi: vi.fn(actual.createAppApi) }
})

const api = vi.mocked(createAppApi).mock.results[0].value

let captured: InternalAxiosRequestConfig[] = []
let responseData: unknown = null

beforeEach(() => {
  captured = []
  responseData = null
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

describe('baseApi', () => {
  // base の 403 (停止中など) は呼び出し元 (アカウントの画面) が扱うので、/account へ飛ばさない
  it('baseURL が /api/base で、403 の移動をしない', () => {
    expect(createAppApi).toHaveBeenCalledWith('base', { redirectOnDenied: false })
    expect(api.defaults.baseURL).toBe('/api/base')
  })

  it('getMe が GET /me の data を返す', async () => {
    responseData = { user_id: 'u-1', status: 'active' }

    const me = await baseApi.getMe()

    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe('/me')
    expect(me).toEqual({ user_id: 'u-1', status: 'active' })
  })

  it('getAssignments が GET /me/assignments の data を返す', async () => {
    responseData = { assignments: [] }

    const result = await baseApi.getAssignments()

    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe('/me/assignments')
    expect(result).toEqual({ assignments: [] })
  })

  it('deviceLogin が POST /devices/login に本文で送る (暗証番号は URL に載せない)', async () => {
    responseData = { account: '12345678', method: 'previous' }
    const input = { account: '12345678', serial: '87654321', pin: '9999', mode: 'previous' as const }

    const result = await baseApi.deviceLogin(input)

    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/devices/login')
    expect(JSON.parse(captured[0].data as string)).toEqual(input)
    expect(captured[0].params).toBeUndefined()
    expect(result).toEqual({ account: '12345678', method: 'previous' })
  })

  // 最新値の開始は、前回値の照会で暗証番号を確かめてから受け付ける (修正ラウンド 1)
  it('startRefresh は暗証番号も本文で送る', async () => {
    responseData = { account: '12345678' }

    await baseApi.startRefresh({ account: '12345678', serial: '87654321', pin: '9999' })

    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/devices/refresh')
    expect(JSON.parse(captured[0].data as string)).toEqual({ account: '12345678', serial: '87654321', pin: '9999' })
    expect(captured[0].params).toBeUndefined()
  })

  it('getRefreshAvailability と getDeviceServices は通信機アカウントをパスに入れる', async () => {
    responseData = { available: true }
    await baseApi.getRefreshAvailability('12345678')
    responseData = { services: [] }
    await baseApi.getDeviceServices('12345678')

    expect(captured.map((config) => [config.method, config.url])).toEqual([
      ['get', '/devices/12345678/refresh-availability'],
      ['get', '/devices/12345678/services'],
    ])
  })

  it('registerTotp が POST /me/totp-registered を呼ぶ', async () => {
    responseData = { admin_state: 'awaiting' }

    const result = await baseApi.registerTotp()

    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/me/totp-registered')
    expect(result).toEqual({ admin_state: 'awaiting' })
  })
})


it('enablePasskeyMfa は本人 API に利用者の識別子を送らない', async () => {
  responseData = { passkey_mfa_enabled: true }
  expect(await baseApi.enablePasskeyMfa()).toEqual(responseData)
  expect(captured[0].method).toBe('post')
  expect(captured[0].url).toBe('/me/passkey-mfa')
  expect(captured[0].data).toBeUndefined()
})
