import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'
import { baseApi } from '@/services/baseApi'
import { useAuthStore } from '@/stores/auth'
import { useLandingStore } from '@/stores/landing'
import type { Me } from '@/stores/me'
import type { DeviceServices, MyDevice } from '@/types/devices'
import { leaveTo, replaceTo } from '@/utils/navigation'
import { installGuards } from './guards'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

vi.mock('@/services/baseApi', () => ({
  baseApi: { getMe: vi.fn(), registerTotp: vi.fn(), getDevices: vi.fn(), getDeviceServices: vi.fn() },
}))

vi.mock('@/utils/navigation', () => ({ leaveTo: vi.fn(), replaceTo: vi.fn() }))

const EMAIL = 'user@example.com'
const Stub = { template: '<div />' }

function makeRouter(): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/login',
        name: 'login',
        component: Stub,
        meta: { requiresAuth: false, layout: 'auth' },
      },
      {
        path: '/login/admin',
        name: 'login-admin',
        component: Stub,
        meta: { requiresAuth: false, layout: 'auth' },
      },
      {
        path: '/',
        component: Stub,
        children: [
          { path: '', name: 'home', component: Stub, meta: { title: 'ホーム' } },
          { path: 'no-title', name: 'no-title', component: Stub },
          { path: 'account', name: 'account', component: Stub, meta: { title: 'アカウント' } },
          { path: 'devices', name: 'devices', component: Stub },
          { path: 'devices/:account', name: 'device-services', component: Stub },
          { path: 'forbidden', name: 'forbidden', component: Stub },
          { path: 'admin', name: 'admin', component: Stub, meta: { requiredGroups: ['admin'] } },
          {
            path: '/billing',
            meta: { appId: 'billing', requiredGroups: ['billing-admin'] },
            children: [{ path: 'settings', name: 'billing-settings', component: Stub }],
          },
        ],
      },
    ],
  })
  installGuards(router)
  return router
}

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user_id: 'u-1',
    email: EMAIL,
    status: 'active',
    kind: 'customer',
    org_name: null,
    is_admin: false,
    admin_state: null,
    mfa_registered: false,
    restore_requires: null,
    ...overrides,
  }
}

function signedOut(): void {
  vi.mocked(getCurrentUser).mockRejectedValue(new Error('No user'))
}

function signedIn(groups: string[] = [], me: Me | Error = makeMe()): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: EMAIL,
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: { idToken: { payload: { 'cognito:groups': groups } } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
  if (me instanceof Error) {
    vi.mocked(baseApi.getMe).mockRejectedValue(me)
  } else {
    vi.mocked(baseApi.getMe).mockResolvedValue(me)
  }
}

describe('installGuards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    document.title = ''
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('未認証で保護ルートへ行くと /login?redirect= になる', async () => {
    signedOut()
    const router = makeRouter()

    await router.push('/no-title')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/no-title')
    expect(baseApi.getMe).not.toHaveBeenCalled()
  })

  it.each(['/login', '/login/admin'])('認証済みで %s へ行くと / へリダイレクトされる', async (path) => {
    signedIn()
    const router = makeRouter()

    await router.push(path)

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('未認証なら /login/admin は通る', async () => {
    signedOut()
    const router = makeRouter()

    await router.push('/login/admin')

    expect(router.currentRoute.value.name).toBe('login-admin')
  })

  it('サインインの後に自分の状態を 1 回だけ読む', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/')
    await router.push('/no-title')

    expect(baseApi.getMe).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('no-title')
  })

  it.each([
    ['停止中', makeMe({ status: 'suspended' }), []],
    ['admin の準備中', makeMe({ is_admin: true, admin_state: 'pending' }), ['admin']],
    ['admin の確認待ち', makeMe({ is_admin: true, admin_state: 'awaiting' }), ['admin']],
  ])('%s は /account だけを通す', async (_label, me, groups) => {
    signedIn(groups, me)
    const router = makeRouter()

    await router.push('/')
    expect(router.currentRoute.value.name).toBe('account')

    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('account')

    await router.push('/account')
    expect(router.currentRoute.value.name).toBe('account')
  })

  it('制限中にサインインの画面へ行くと /account へ移る', async () => {
    signedIn([], makeMe({ status: 'suspended' }))
    const router = makeRouter()

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('account')
  })

  it('自分の状態が読めなければ /account だけを通す', async () => {
    signedIn([], new Error('Network Error'))
    const router = makeRouter()

    await router.push('/no-title')

    expect(router.currentRoute.value.name).toBe('account')
  })

  it('有効な admin は admin の画面へ進める', async () => {
    signedIn(['admin'], makeMe({ is_admin: true, admin_state: 'active' }))
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('admin')
  })

  it('requiredGroups に所属していないと /forbidden', async () => {
    signedIn(['viewer'])
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('親ルートの requiredGroups が子ルートにも効く', async () => {
    signedIn(['viewer'])
    const router = makeRouter()

    await router.push('/billing/settings')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('afterEach で document.title が「{title} | SERVICE-PLATFORM」になる', async () => {
    vi.stubEnv('VITE_SITE_ENV', 'prod')
    signedIn()
    const router = makeRouter()

    await router.push('/')
    expect(document.title).toBe('ホーム | SERVICE-PLATFORM')

    await router.push('/no-title')
    expect(document.title).toBe('SERVICE-PLATFORM')
  })

  // VITE_SITE_ENV 未設定は検証サイト扱い
  it('検証サイトでは document.title に [検証] が前置される', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/')
    expect(document.title).toBe('[検証] ホーム | SERVICE-PLATFORM')

    await router.push('/no-title')
    expect(document.title).toBe('[検証] SERVICE-PLATFORM')
  })

  it('checkAuth は初回だけ呼ばれる', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/')
    await router.push('/no-title')

    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })
})

const ACCOUNT = '12345678'
const CAMERA_URL = `/camera/?account=${ACCOUNT}`

function myDevice(overrides: Partial<MyDevice> = {}): MyDevice {
  return {
    account: ACCOUNT,
    device_period_id: '01JBZ00000000000000PERIOD',
    org_name: '組織A',
    basis: 'user',
    assignment_id: 'A-1',
    group_id: null,
    group_label: null,
    serial: '87654321',
    service_count: 1,
    ...overrides,
  }
}

function loggedIn(count = 1): DeviceServices {
  return {
    account: ACCOUNT,
    org_name: '組織A',
    location_name: null,
    method: 'previous',
    logged_in_at: '2026-11-05T00:00:00Z',
    expires_at: '2026-11-05T12:00:00Z',
    services: Array.from({ length: count }, (_, index) => ({
      id: index === 0 ? 'camera' : `other${index}`,
      name: 'カメラ画像判定',
      url: index === 0 ? CAMERA_URL : `/other${index}/?account=${ACCOUNT}`,
      starts_at: '2026-11-01T00:00:00Z',
      ends_at: null,
    })),
  }
}

function loginRequired(): unknown {
  return Object.assign(new Error('Request failed with status code 403'), {
    response: {
      status: 403,
      data: { ok: false, data: null, error: { code: 'DEVICE_LOGIN_REQUIRED', message: '' }, meta: null },
    },
  })
}

function visible(devices: MyDevice[]): void {
  vi.mocked(baseApi.getDevices).mockResolvedValue({ devices })
}

/** サービスの一覧の API の応答を、テストが決めた時に返す */
function delayedServices(): { resolve: (value: DeviceServices) => void; reject: (reason: unknown) => void } {
  const handlers = { resolve: (_value: DeviceServices) => {}, reject: (_reason: unknown) => {} }
  vi.mocked(baseApi.getDeviceServices).mockReturnValue(
    new Promise<DeviceServices>((resolve, reject) => Object.assign(handlers, { resolve, reject })),
  )
  return handlers
}

async function judging(): Promise<void> {
  await vi.waitFor(() => expect(baseApi.getDeviceServices).toHaveBeenCalled())
}

// : 着地 (読み込んだ直後の / とサインインの直後) だけ、1 台 1 サービスなら途中の画面を飛ばす
describe('着地の近道', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('ログイン記録が無ければ、入り方の選択と一覧を飛ばして通信機の暗証番号の段へ', async () => {
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(loginRequired())
    const router = makeRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('devices')
    expect(useLandingStore().account).toBe(ACCOUNT)
  })

  it('ログイン記録が有効なら、暗証番号の段とサービスの一覧も飛ばしてサービスへ (履歴に残さない)', async () => {
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(loggedIn())
    const router = makeRouter()

    await router.push('/')

    expect(replaceTo).toHaveBeenCalledWith(CAMERA_URL)
    expect(leaveTo).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).not.toBe('home')
  })

  it('サインインの直後 (/login から /) も近道する', async () => {
    signedOut()
    const router = makeRouter()
    await router.push('/login')
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(loginRequired())
    const { useAuthStore } = await import('@/stores/auth')
    await useAuthStore().checkAuth()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('devices')
  })

  it('アプリの中でホームへ移ったときは近道せず入り方の選択を出す (往復しない)', async () => {
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(loggedIn())
    const router = makeRouter()
    await router.push('/account')

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('home')
    expect(replaceTo).not.toHaveBeenCalled()
  })

  it.each([
    ['2 台', [myDevice(), myDevice({ account: '23456789' })]],
    ['サービスが 0', [myDevice({ service_count: 0 })]],
    ['サービスが 2', [myDevice({ service_count: 2 })]],
    ['通信機が無い', []],
  ])('%s なら今までどおり入り方の選択', async (_label, devices) => {
    signedIn()
    visible(devices)
    const router = makeRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('home')
    expect(useLandingStore().account).toBeNull()
    expect(baseApi.getDeviceServices).not.toHaveBeenCalled()
  })

  it('admin は近道しない (一覧を読まない)', async () => {
    signedIn(['admin'], makeMe({ is_admin: true, admin_state: 'active' }))
    visible([myDevice()])
    const router = makeRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('home')
    expect(baseApi.getDevices).not.toHaveBeenCalled()
  })

  it('一覧が読めなければ入り方の選択のまま', async () => {
    signedIn()
    vi.mocked(baseApi.getDevices).mockRejectedValue(new Error('Network Error'))
    const router = makeRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('/devices を明示的に開いたときは近道の印を付けない (一覧を出す)', async () => {
    signedIn()
    visible([myDevice()])
    const router = makeRouter()

    await router.push('/devices')

    expect(router.currentRoute.value.name).toBe('devices')
    expect(useLandingStore().account).toBeNull()
    expect(baseApi.getDevices).not.toHaveBeenCalled()
  })

  it('/devices/{account} を明示的に開いたときはサービスへ移らない', async () => {
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(loggedIn())
    const router = makeRouter()

    await router.push(`/devices/${ACCOUNT}`)

    expect(router.currentRoute.value.name).toBe('device-services')
    expect(replaceTo).not.toHaveBeenCalled()
    expect(leaveTo).not.toHaveBeenCalled()
  })

  // 判定の待機の間に移った画面や替わった利用者を、前の判定の結果で上書きしない
  it('判定中に /account へ移ったら、遅れて届いたログイン記録の応答でサービスへ移らない', async () => {
    signedIn()
    visible([myDevice()])
    const services = delayedServices()
    const router = makeRouter()
    const landing = router.push('/')
    await judging()

    await router.push('/account')
    services.resolve(loggedIn())
    await landing

    expect(replaceTo).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('account')
  })

  it('判定中に /account へ移ったら、遅れて届いた DEVICE_LOGIN_REQUIRED で /devices へ移らない', async () => {
    signedIn()
    visible([myDevice()])
    const services = delayedServices()
    const router = makeRouter()
    const landing = router.push('/')
    await judging()

    await router.push('/account')
    services.reject(loginRequired())
    await landing

    expect(router.currentRoute.value.name).toBe('account')
    expect(useLandingStore().account).toBeNull()
  })

  it('判定中に /account へ移ったら、遅れて終わった判定がタイトルを書き換えない', async () => {
    signedIn()
    visible([myDevice()])
    const services = delayedServices()
    const router = makeRouter()
    const landing = router.push('/')
    await judging()

    await router.push('/account')
    services.reject(loginRequired())
    await landing

    expect(document.title).toBe('[検証] アカウント | SERVICE-PLATFORM')
  })

  it('判定中にサインアウトして別の admin が入ったら、前の利用者の応答でサービスへ移らない', async () => {
    signedIn()
    visible([myDevice()])
    const services = delayedServices()
    const router = makeRouter()
    const landing = router.push('/')
    await judging()

    const auth = useAuthStore()
    await auth.logout()
    signedIn(['admin'], makeMe({ is_admin: true, admin_state: 'active' }))
    await auth.checkAuth()
    services.resolve(loggedIn())
    await landing

    expect(replaceTo).not.toHaveBeenCalled()
    expect(useLandingStore().account).toBeNull()
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('近道の印は通信機ログインの画面を離れると消える', async () => {
    signedIn()
    visible([myDevice()])
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(loginRequired())
    const router = makeRouter()
    await router.push('/')
    expect(useLandingStore().account).toBe(ACCOUNT)

    await router.push('/account')

    expect(useLandingStore().account).toBeNull()
  })
})
