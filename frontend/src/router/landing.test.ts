import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { START_LOCATION, type RouteLocationNormalized } from 'vue-router'
import { baseApi } from '@/services/baseApi'
import type { DeviceServices, MyDevice, ServiceLink } from '@/types/devices'
import { isLanding, launchTarget, resolveLanding, shortcutDevice } from './landing'

vi.mock('@/services/baseApi', () => ({
  baseApi: { getDevices: vi.fn(), getDeviceServices: vi.fn(), getMe: vi.fn() },
}))

const ACCOUNT = '12345678'
const CAMERA_URL = `/camera/?account=${ACCOUNT}`

function device(overrides: Partial<MyDevice> = {}): MyDevice {
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

function camera(overrides: Partial<ServiceLink> = {}): ServiceLink {
  return { id: 'camera', name: 'カメラ画像判定', url: CAMERA_URL, ...overrides }
}

function services(links: ServiceLink[]): DeviceServices {
  return {
    account: ACCOUNT,
    org_name: '組織A',
    location_name: null,
    method: 'previous',
    logged_in_at: '2026-11-05T00:00:00Z',
    expires_at: '2026-11-05T12:00:00Z',
    services: links.map((link) => ({ ...link, starts_at: '2026-11-01T00:00:00Z', ends_at: null })),
  }
}

function apiError(status: number, code: string): unknown {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { ok: false, data: null, error: { code, message: code }, meta: null } },
  })
}

function from(name: string): RouteLocationNormalized {
  return { ...START_LOCATION, name, path: `/${name}`, fullPath: `/${name}` } as RouteLocationNormalized
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
})

// : admin でない利用者が通信機 1 台とサービス 1 つだけなら、途中の画面を飛ばす
describe('shortcutDevice', () => {
  it('admin でなく、見える通信機が 1 台で、サービスが 1 つなら、その通信機', () => {
    const only = device()

    expect(shortcutDevice([only], false)).toBe(only)
  })

  it.each([
    ['0 台', []],
    ['2 台', [device(), device({ account: '23456789' })]],
    ['サービスが 0', [device({ service_count: 0 })]],
    ['サービスが 2', [device({ service_count: 2 })]],
    ['サービスの数が分からない', [device({ service_count: undefined })]],
  ])('%s なら近道しない', (_label, devices) => {
    expect(shortcutDevice(devices, false)).toBeNull()
  })

  it('everyone の通信機でも 1 台 1 サービスなら近道する', () => {
    const only = device({ basis: 'everyone', assignment_id: null, serial: null })

    expect(shortcutDevice([only], false)).toBe(only)
  })

  it('admin は近道しない', () => {
    expect(shortcutDevice([device()], true)).toBeNull()
  })
})

describe('launchTarget', () => {
  it('サービスが 1 つならその起動先', () => {
    expect(launchTarget([camera()])).toBe(CAMERA_URL)
  })

  it('同じサービスの許可が 2 つあっても 1 つと数える', () => {
    expect(launchTarget([camera(), camera()])).toBe(CAMERA_URL)
  })

  it.each([
    ['0', []],
    ['2', [camera(), camera({ id: 'other', name: '別', url: `/other/?account=${ACCOUNT}` })]],
  ])('サービスが %s なら起動しない', (_label, links) => {
    expect(launchTarget(links)).toBeNull()
  })

  it.each(['https://example.com/camera/', '//example.com/camera/', 'camera/'])(
    '同じサイトの絶対パスでない起動先 %s へは移らない',
    (url) => {
      expect(launchTarget([camera({ url })])).toBeNull()
    },
  )
})

describe('isLanding', () => {
  it('読み込んだ直後の最初の画面とサインインの直後は着地', () => {
    expect(isLanding(START_LOCATION)).toBe(true)
    expect(isLanding(from('login'))).toBe(true)
    expect(isLanding(from('login-admin'))).toBe(true)
  })

  it.each(['account', 'devices', 'device-services'])('アプリの中で %s からホームへ移るのは着地ではない', (name) => {
    expect(isLanding(from(name))).toBe(false)
  })
})

describe('resolveLanding', () => {
  it('通信機ログインの記録が有効で、サービスが 1 つなら、そのサービスへ', async () => {
    vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [device()] })
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(services([camera()]))

    expect(await resolveLanding(false)).toEqual({ kind: 'launch', account: ACCOUNT, url: CAMERA_URL })
    expect(baseApi.getDeviceServices).toHaveBeenCalledWith(ACCOUNT)
  })

  it('通信機ログインの記録が無ければ、その通信機の暗証番号の段へ', async () => {
    vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [device()] })
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(apiError(403, 'DEVICE_LOGIN_REQUIRED'))

    expect(await resolveLanding(false)).toEqual({ kind: 'login', account: ACCOUNT })
  })

  it.each([
    ['割り当てが外れた', apiError(403, 'DEVICE_NOT_ASSIGNED')],
    ['サーバの失敗', apiError(500, 'INTERNAL_ERROR')],
    ['通信の失敗', new Error('Network Error')],
  ])('サービスの一覧が %s で読めなければ、入り方の選択のまま', async (_label, error) => {
    vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [device()] })
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(error)

    expect(await resolveLanding(false)).toEqual({ kind: 'start' })
  })

  it('確かめ直したサービスが 1 つでなければ (条件が崩れた)、入り方の選択のまま', async () => {
    vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [device()] })
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(services([]))

    expect(await resolveLanding(false)).toEqual({ kind: 'start' })
  })

  it('2 台なら入り方の選択のまま (サービスの一覧を読まない)', async () => {
    vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [device(), device({ account: '23456789' })] })

    expect(await resolveLanding(false)).toEqual({ kind: 'start' })
    expect(baseApi.getDeviceServices).not.toHaveBeenCalled()
  })

  it('通信機の一覧が読めなければ、入り方の選択のまま', async () => {
    vi.mocked(baseApi.getDevices).mockRejectedValue(apiError(500, 'INTERNAL_ERROR'))

    expect(await resolveLanding(false)).toEqual({ kind: 'start' })
    expect(baseApi.getDeviceServices).not.toHaveBeenCalled()
  })

  it('admin は一覧を読まずに入り方の選択のまま', async () => {
    expect(await resolveLanding(true)).toEqual({ kind: 'start' })
    expect(baseApi.getDevices).not.toHaveBeenCalled()
  })
})
