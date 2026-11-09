import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { baseApi } from '@/services/baseApi'
import type { DeviceServices } from '@/types/devices'
import { leaveTo } from '@/utils/navigation'
import DeviceServicesView from './DeviceServicesView.vue'

vi.mock('@/utils/navigation', () => ({ leaveTo: vi.fn() }))

vi.mock('@/services/baseApi', () => ({
  baseApi: {
    getAssignments: vi.fn(),
    deviceLogin: vi.fn(),
    startRefresh: vi.fn(),
    getRefreshAvailability: vi.fn(),
    getDeviceServices: vi.fn(),
  },
}))

const ACCOUNT = '12345678'

function services(overrides: Partial<DeviceServices> = {}): DeviceServices {
  return {
    account: ACCOUNT,
    org_name: '組織A',
    location_name: '見本の現場',
    method: 'previous',
    logged_in_at: '2026-11-04T03:00:00Z',
    expires_at: '2026-11-04T15:00:00Z',
    services: [
      {
        id: 'camera',
        name: 'カメラ画像判定',
        url: `/camera/?account=${ACCOUNT}`,
        starts_at: '2026-11-01T00:00:00Z',
        ends_at: null,
      },
    ],
    ...overrides,
  }
}

function apiError(status: number, code: string): unknown {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { ok: false, data: null, error: { code, message: code }, meta: null } },
  })
}

let wrapper: VueWrapper
let router: Router

function el(id: string): HTMLElement | null {
  return document.querySelector(`[data-test="${id}"]`)
}

async function show({ keepAlive = false } = {}): Promise<void> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/devices', name: 'devices', component: { template: '<div />' } },
      { path: '/devices/:account', name: 'device-services', component: DeviceServicesView, props: true },
    ],
  })
  await router.push(`/devices/${ACCOUNT}`)
  await router.isReady()
  // ShellLayout と同じく keep-alive の中に置ける
  const template = keepAlive
    ? '<RouterView v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></RouterView>'
    : '<RouterView />'
  wrapper = mount(
    { template },
    { global: { plugins: [createVuetify({ components, directives }), router] }, attachTo: document.body },
  )
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
})

afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('DeviceServicesView', () => {
  it('通信機の要約と、許可されたサービスのカードを出す', async () => {
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(services())

    await show()

    expect(baseApi.getDeviceServices).toHaveBeenCalledWith(ACCOUNT)
    const header = el('device-summary')?.textContent ?? ''
    expect(header).toContain(ACCOUNT)
    expect(header).toContain('組織A')
    expect(header).toContain('見本の現場')
    expect(header).toContain('前回値')
    expect(header).toContain('2026-11-05 00:00')
    expect(el('service-camera')?.textContent).toContain('カメラ画像判定')
  })

  // カメラの SPA は /camera/ の別ビルド。同じタブでページごと移る (ADR D6、)
  it('カメラのカードを押すと同じタブで /camera/?account= へ移る', async () => {
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(services())

    await show()

    expect(el('service-camera')?.textContent).not.toContain('準備中')
    const button = el('launch-camera') as HTMLButtonElement
    expect(button.disabled).toBe(false)
    button.click()
    await flushPromises()

    expect(leaveTo).toHaveBeenCalledWith(`/camera/?account=${ACCOUNT}`)
  })

  it.each(['https://example.com/camera/', '//example.com/camera/', 'javascript:alert(1)', ''])(
    '起動先が同じサイトのパスでなければ (%s) 移らない',
    async (url) => {
      const base = services()
      vi.mocked(baseApi.getDeviceServices).mockResolvedValue({
        ...base,
        services: [{ ...base.services[0], url }],
      })

      await show()
      ;(el('launch-camera') as HTMLButtonElement).click()
      await flushPromises()

      expect(leaveTo).not.toHaveBeenCalled()
    },
  )

  it('許可されたサービスが無ければ案内を出す', async () => {
    vi.mocked(baseApi.getDeviceServices).mockResolvedValue(services({ services: [] }))

    await show()

    expect(el('no-services')?.textContent).toContain('使えるサービスはありません')
  })

  it.each([
    ['DEVICE_LOGIN_REQUIRED', '通信機ログイン'],
    ['DEVICE_NOT_ASSIGNED', '割り当てられていません'],
  ])('%s なら通信機ログインからやり直す案内を出す', async (code, text) => {
    vi.mocked(baseApi.getDeviceServices).mockRejectedValue(apiError(403, code))

    await show()

    expect(el('services-error')?.textContent).toContain(text)
    el('back-to-login')?.click()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('devices'))
  })

  it('読めなければ読み直せる', async () => {
    vi.mocked(baseApi.getDeviceServices).mockRejectedValueOnce(new Error('Network Error'))
    vi.mocked(baseApi.getDeviceServices).mockResolvedValueOnce(services())
    await show()
    expect(el('services-error')).not.toBeNull()

    el('services-retry')?.click()
    await flushPromises()

    expect(el('service-camera')).not.toBeNull()
  })

  it('keep-alive で同じ通信機へ戻ったときも読み直す (許可やログインの期限が変わっている)', async () => {
    vi.mocked(baseApi.getDeviceServices).mockRejectedValueOnce(apiError(403, 'DEVICE_LOGIN_REQUIRED'))
    vi.mocked(baseApi.getDeviceServices).mockResolvedValueOnce(services())
    await show({ keepAlive: true })
    expect(el('services-error')).not.toBeNull()

    await router.push('/devices')
    await flushPromises()
    await router.push(`/devices/${ACCOUNT}`)
    await flushPromises()

    expect(baseApi.getDeviceServices).toHaveBeenCalledTimes(2)
    expect(el('services-error')).toBeNull()
    expect(el('service-camera')).not.toBeNull()
  })
})
