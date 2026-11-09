import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { fetchAuthSession, getCurrentUser, signOut } from 'aws-amplify/auth'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import { useMeStore } from '@/stores/me'
import { createAppApi, extractApiError } from './http'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

vi.mock('@/services/baseApi', () => ({
  baseApi: {
    getMe: vi.fn(async () => ({
      user_id: 'u-1',
      email: 'user@example.com',
      status: 'active',
      kind: 'customer',
      org_name: null,
      is_admin: false,
      admin_state: null,
      mfa_registered: false,
    })),
    registerTotp: vi.fn(),
  },
}))

// 実ガードを載せたルーターを @/router の代わりに使い、401 → ストア未認証化 → /login
// 到達までの継ぎ目をそのまま検証する（push のスパイだけではガードを通らない）
vi.mock('@/router', async () => {
  const { createMemoryHistory, createRouter } = await import('vue-router')
  const { installGuards } = await import('@/router/guards')
  const Stub = { template: '<div />' }
  const testRouter = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: Stub, meta: { requiresAuth: false } },
      { path: '/', name: 'home', component: Stub },
      { path: '/account', name: 'account', component: Stub },
      { path: '/billing/settings', name: 'billing-settings', component: Stub },
    ],
  })
  installGuards(testRouter)
  return { default: testRouter }
})

const APP_ID = 'sample-report'
const EMAIL = 'user@example.com'

function signedIn(idToken: string | null = 'id-token-1'): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: EMAIL,
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: idToken === null ? undefined : { idToken: { payload: {}, toString: () => idToken } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
}

async function startAtBillingSettings(): Promise<void> {
  await router.push('/billing/settings')
  expect(router.currentRoute.value.name).toBe('billing-settings')
}

function captureRequests(api: AxiosInstance): InternalAxiosRequestConfig[] {
  const captured: InternalAxiosRequestConfig[] = []
  const adapter: AxiosAdapter = async (config) => {
    captured.push(config)
    return { data: null, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse
  }
  api.defaults.adapter = adapter
  return captured
}

function failWith(api: AxiosInstance, status: number, data: unknown = {}): void {
  const adapter: AxiosAdapter = async (config) => {
    throw Object.assign(new Error(`Request failed with status code ${status}`), {
      config,
      response: { status, data, statusText: '', headers: {}, config },
    })
  }
  api.defaults.adapter = adapter
}

function envelope(code: string, message = code): unknown {
  return { ok: false, data: null, error: { code, message }, meta: null }
}

describe('createAppApi', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    signedIn()
  })

  it('baseURL が /api/{appId} になる', () => {
    const api = createAppApi(APP_ID)

    expect(api.defaults.baseURL).toBe(`/api/${APP_ID}`)
    expect(api.defaults.timeout).toBe(30000)
  })

  it('リクエストに Authorization: Bearer <idToken> が付く', async () => {
    const api = createAppApi(APP_ID)
    const captured = captureRequests(api)

    await api.get('/invoices')

    expect(captured[0].headers.Authorization).toBe('Bearer id-token-1')
  })

  it('トークンが取れないときは Authorization を付けない', async () => {
    signedIn(null)
    const api = createAppApi(APP_ID)
    const captured = captureRequests(api)

    await api.get('/invoices')

    expect(captured[0].headers.Authorization).toBeUndefined()
  })

  // JWT authorizer の 401 はエンベロープを持たない。401 だけをサインアウトの合図にする (計画 2.6)
  it.each([
    ['JWT authorizer の 401', { message: 'Unauthorized' }],
    ['エンベロープの UNAUTHORIZED', envelope('UNAUTHORIZED')],
  ])('%s で signOut が呼ばれ /login へ遷移する', async (_label, data) => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 401, data)

    await expect(api.get('/invoices')).rejects.toThrow('Request failed with status code 401')

    expect(signOut).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/billing/settings')
  })

  it('401 後は auth ストアが未認証になりガードに跳ね返されない', async () => {
    await startAtBillingSettings()
    expect(useAuthStore().isAuthenticated).toBe(true)
    const api = createAppApi(APP_ID)
    failWith(api, 401)

    await expect(api.get('/invoices')).rejects.toThrow()

    expect(useAuthStore().isAuthenticated).toBe(false)
    expect(router.currentRoute.value.name).not.toBe('home')
  })

  it.each([
    'USER_SUSPENDED',
    'USER_NOT_REGISTERED',
    'ADMIN_NOT_ACTIVE',
    'ADMIN_SESSION_STALE',
    'ADMIN_MFA_MISSING',
  ])('403 %s はサインアウトせず理由を控えて /account へ移る', async (code) => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 403, envelope(code))

    await expect(api.get('/invoices')).rejects.toThrow('Request failed with status code 403')

    expect(signOut).not.toHaveBeenCalled()
    expect(useMeStore().denied).toBe(code)
    expect(router.currentRoute.value.name).toBe('account')
  })

  it('403 で移った後は me を読み直す', async () => {
    await startAtBillingSettings()
    const me = useMeStore()
    const load = vi.spyOn(me, 'load')
    const api = createAppApi(APP_ID)
    failWith(api, 403, envelope('USER_SUSPENDED'))

    await expect(api.get('/invoices')).rejects.toThrow()

    expect(load).toHaveBeenCalled()
  })

  it('業務の 403 (自分自身の操作など) は移動しない', async () => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 403, envelope('SELF_OPERATION_FORBIDDEN'))

    await expect(api.get('/invoices')).rejects.toThrow()

    expect(useMeStore().denied).toBeNull()
    expect(router.currentRoute.value.name).toBe('billing-settings')
  })

  it('redirectOnDenied: false なら 403 でも移動しない', async () => {
    await startAtBillingSettings()
    const api = createAppApi('base', { redirectOnDenied: false })
    failWith(api, 403, envelope('USER_SUSPENDED'))

    await expect(api.get('/me')).rejects.toThrow()

    expect(router.currentRoute.value.name).toBe('billing-settings')
  })

  it('401・403 以外はそのまま reject される', async () => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 500)

    await expect(api.get('/invoices')).rejects.toThrow('Request failed with status code 500')

    expect(signOut).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('billing-settings')
  })
})

describe('extractApiError', () => {
  it('エンベロープの error を取り出す', () => {
    const failure = {
      response: {
        status: 403,
        data: {
          ok: false,
          data: null,
          error: { code: 'SP-E003', message: '権限がありません' },
          meta: null,
        },
      },
    }

    expect(extractApiError(failure)).toEqual({ code: 'SP-E003', message: '権限がありません' })
    const noError = { response: { status: 500, data: { ok: false, error: null } } }
    expect(extractApiError(noError)).toBeNull()
    expect(extractApiError({ response: { data: { error: { detail: 'x' } } } })).toBeNull()
    expect(extractApiError(new Error('Network Error'))).toBeNull()
    expect(extractApiError(null)).toBeNull()
  })
})
