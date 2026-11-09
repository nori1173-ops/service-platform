import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { baseApi } from '@/services/baseApi'
import { MESSAGES, useDeviceStore } from '@/stores/device'
import { useLandingStore } from '@/stores/landing'
import type { DeviceLoginResult, MyDevice } from '@/types/devices'
import { leaveTo } from '@/utils/navigation'
import DeviceLoginView from './DeviceLoginView.vue'

vi.mock('@/services/baseApi', () => ({
  baseApi: {
    getDevices: vi.fn(),
    deviceLogin: vi.fn(),
    startRefresh: vi.fn(),
    getRefreshAvailability: vi.fn(),
    getDeviceServices: vi.fn(),
    getMe: vi.fn(),
  },
}))

vi.mock('@/utils/navigation', () => ({ leaveTo: vi.fn(), replaceTo: vi.fn() }))

const ACCOUNT = '12345678'
const OTHER = '23456789'
const SERIAL = '87654321'
const PIN = '9999'

function assignment(overrides: Partial<MyDevice> = {}): MyDevice {
  return {
    assignment_id: `A-${overrides.account ?? ACCOUNT}`,
    device_period_id: '01JBZ00000000000000PERIOD',
    account: ACCOUNT,
    org_name: '組織A',
    valid_from: '2026-11-01T00:00:00Z',
    valid_to: null,
    serial: SERIAL,
    basis: 'user',
    group_id: null,
    group_label: null,
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

async function type(id: string, value: string): Promise<void> {
  const input = el(id)?.querySelector('input') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function click(id: string): Promise<void> {
  el(id)?.click()
  await flushPromises()
}

async function show(assignments: MyDevice[]): Promise<void> {
  vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: assignments })
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/devices', name: 'devices', component: DeviceLoginView },
      { path: '/devices/:account', name: 'device-services', component: { template: '<div />' }, props: true },
      { path: '/account', name: 'account', component: { template: '<div />' } },
    ],
  })
  await router.push('/devices')
  await router.isReady()
  wrapper = mount(
    { template: '<RouterView />' },
    { global: { plugins: [createVuetify({ components, directives }), router] }, attachTo: document.body },
  )
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
    account: ACCOUNT,
    available: true,
    reason: null,
    in_progress: false,
  })
})

afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('DeviceLoginView', () => {
  it('割り当てられた通信機 (人宛・グループ宛) と誰でも使える通信機の 2 つの表に出す', async () => {
    await show([
      assignment(),
      assignment({ account: OTHER, serial: null, org_name: '組織B', basis: 'group', group_label: '会社A > 部署B' }),
      assignment({ account: '34567890', assignment_id: null, basis: 'everyone', org_name: '組織C', serial: null }),
    ])

    expect(el(`assignment-${ACCOUNT}`)?.textContent).toContain('組織A')
    expect(el(`assignment-${ACCOUNT}`)?.textContent).toContain(SERIAL)
    expect(el(`assignment-${OTHER}`)?.textContent).toContain('未登録')
    expect(el(`assignment-${OTHER}`)?.textContent).toContain('グループ: 会社A > 部署B')
    expect(el('assignment-34567890')).toBeNull()
    expect(el('everyone-34567890')?.textContent).toContain('組織C')
    expect(document.body.textContent).toContain('通信機を選び、暗証番号を入力してください')
  })

  it('どちらも無ければ案内を出す', async () => {
    await show([])

    expect(el('no-assignments')?.textContent).toContain('使える通信機がありません')
    expect(el('field-pin')).toBeNull()
  })

  it('10 件を超えたら通信機アカウントか組織名で絞る欄を出す', async () => {
    const many = Array.from({ length: 11 }, (_, index) =>
      assignment({
        account: String(30000000 + index),
        assignment_id: null,
        basis: 'everyone',
        org_name: index === 3 ? '探す組織' : '組織X',
        serial: null,
      }),
    )
    await show(many)

    expect(el('device-filter')).not.toBeNull()
    const input = el('device-filter')?.querySelector('input') as HTMLInputElement
    input.value = '探す'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(document.querySelectorAll('[data-test^="everyone-"]')).toHaveLength(1)
    expect(el('everyone-30000003')).not.toBeNull()
  })

  it('10 件以下なら絞る欄を出さない', async () => {
    await show([assignment()])

    expect(el('device-filter')).toBeNull()
  })

  it('1 件だけなら選んだ状態で始まり、控えがあれば暗証番号だけを入力する', async () => {
    await show([assignment()])

    expect(el('selected-account')?.textContent).toContain(ACCOUNT)
    expect(el('field-serial')).toBeNull()
    const pin = el('field-pin')?.querySelector('input') as HTMLInputElement
    expect(pin.type).toBe('password')
    expect(pin.getAttribute('autocomplete')).toBe('off')
  })

  it('製造番号の控えが無ければ製造番号も入力する', async () => {
    await show([assignment(), assignment({ account: OTHER, serial: null })])

    await click(`assignment-${OTHER}`)

    expect(el('selected-account')?.textContent).toContain(OTHER)
    expect(el('field-serial')).not.toBeNull()
  })

  it('前回値を読み込むとサービスの一覧へ進み、暗証番号を捨てる', async () => {
    vi.mocked(baseApi.deviceLogin).mockResolvedValue({
      account: ACCOUNT,
      method: 'previous',
      location_name: null,
      observation: {},
      devices: [],
      services: [],
    })
    await show([assignment()])
    await type('field-pin', PIN)

    await click('load-previous')

    expect(baseApi.deviceLogin).toHaveBeenCalledWith({ account: ACCOUNT, serial: SERIAL, pin: PIN, mode: 'previous' })
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('device-services'))
    expect(router.currentRoute.value.params.account).toBe(ACCOUNT)
    expect(useDeviceStore().pin).toBe('')
  })

  it('照会に失敗したら案内を出し、暗証番号の欄を空にする', async () => {
    vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'PIN_MISMATCH'))
    await show([assignment()])
    await type('field-pin', PIN)

    await click('load-previous')

    expect(el('login-message')?.textContent).toContain(MESSAGES.pinMismatch)
    expect((el('field-pin')?.querySelector('input') as HTMLInputElement).value).toBe('')
    expect(router.currentRoute.value.name).toBe('devices')
  })

  it('最新値は警告の後に始め、待機の経過と中断を出す', async () => {
    vi.mocked(baseApi.startRefresh).mockResolvedValue({
      account: ACCOUNT,
      accepted_at: '2026-11-04T03:00:00Z',
      expires_at: '2026-11-04T03:10:00Z',
      timeout_seconds: 600,
      acceptance_id: '01JBZ0000000000000ACCEPT1',
      poll_token: 'poll-token-for-tests-0123456789abcdefghijklmn',
      resumed: false,
    })
    await show([assignment()])
    await type('field-pin', PIN)

    await click('start-latest')
    expect(el('refresh-warning')?.textContent).toContain(MESSAGES.refreshWarning)
    await click('confirm-refresh')

    expect(el('waiting')?.textContent).toContain(MESSAGES.waiting)
    await click('cancel-wait')
    expect(el('login-message')?.textContent).toContain(MESSAGES.waitCancelled)
    expect(el('waiting')).toBeNull()
  })

  it('運用中は最新値を押せない', async () => {
    vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
      account: ACCOUNT,
      available: false,
      reason: 'in_operation',
      in_progress: false,
    })
    await show([assignment()])
    await type('field-pin', PIN)

    expect((el('start-latest') as HTMLButtonElement).disabled).toBe(true)
    expect((el('load-previous') as HTMLButtonElement).disabled).toBe(false)
    expect(el('refresh-reason')).toBeNull()
  })

  // 停止・再招待・admin の状態で断られたら、状態を読み直してアカウントの画面へ移る
  it('利用者の状態で断られたらアカウントの画面へ移る', async () => {
    vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'USER_SUSPENDED'))
    await show([assignment()])
    await type('field-pin', PIN)

    await click('load-previous')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('account'))
    expect(useDeviceStore().pin).toBe('')
  })

  it('画面を離れると暗証番号を捨てる', async () => {
    await show([assignment()])
    await type('field-pin', PIN)
    expect(useDeviceStore().pin).toBe(PIN)

    wrapper.unmount()

    expect(useDeviceStore().pin).toBe('')
  })
})

function loginResult(services: DeviceLoginResult['services']): DeviceLoginResult {
  return { account: ACCOUNT, method: 'previous', location_name: null, observation: {}, devices: [], services }
}

const CAMERA = { id: 'camera', name: 'カメラ画像判定', url: `/camera/?account=${ACCOUNT}` }

/** 着地の近道 (ガードが付けた印) で来た状態 */
async function showViaShortcut(devices: MyDevice[]): Promise<void> {
  useLandingStore().enter(ACCOUNT)
  await show(devices)
}

// : 着地の近道で来たときは、通信機を選んだ状態の暗証番号の段だけを出す
describe('DeviceLoginView の近道', () => {
  it('近道で来たら一覧の表を出さず、案内と暗証番号の段を出す', async () => {
    await showViaShortcut([assignment({ service_count: 1 })])

    expect(el('shortcut-notice')?.textContent).toContain('1 台')
    expect(document.body.textContent).toContain('暗証番号を入力してください')
    expect(document.body.textContent).not.toContain('通信機を選び')
    expect(el(`assignment-${ACCOUNT}`)).toBeNull()
    expect(el('selected-account')?.textContent).toContain(ACCOUNT)
    expect(el('field-pin')).not.toBeNull()
  })

  it('製造番号の控えが無ければ、近道でも製造番号を入力する', async () => {
    await showViaShortcut([assignment({ service_count: 1, serial: null, basis: 'everyone', assignment_id: null })])

    expect(el('field-serial')).not.toBeNull()
    expect(el(`everyone-${ACCOUNT}`)).toBeNull()
  })

  it('「一覧を出す」で今までの表に戻る', async () => {
    await showViaShortcut([assignment({ service_count: 1 })])

    await click('shortcut-show-list')

    expect(el('shortcut-notice')).toBeNull()
    expect(el(`assignment-${ACCOUNT}`)).not.toBeNull()
    expect(useLandingStore().account).toBeNull()
  })

  it('近道でログインが成立し、サービスが 1 つなら、サービスの一覧を飛ばしてそのサービスへ', async () => {
    vi.mocked(baseApi.deviceLogin).mockResolvedValue(loginResult([CAMERA]))
    await showViaShortcut([assignment({ service_count: 1 })])
    await type('field-pin', PIN)

    await click('load-previous')

    await vi.waitFor(() => expect(leaveTo).toHaveBeenCalledWith(CAMERA.url))
    expect(router.currentRoute.value.name).toBe('devices')
    expect(useDeviceStore().pin).toBe('')
  })

  it('近道でも、ログインの応答のサービスが 1 つでなければサービスの一覧へ', async () => {
    vi.mocked(baseApi.deviceLogin).mockResolvedValue(
      loginResult([CAMERA, { id: 'other', name: '別', url: `/other/?account=${ACCOUNT}` }]),
    )
    await showViaShortcut([assignment({ service_count: 1 })])
    await type('field-pin', PIN)

    await click('load-previous')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('device-services'))
    expect(leaveTo).not.toHaveBeenCalled()
  })

  it('明示的に開いたときは、1 台 1 サービスでもログインの後はサービスの一覧へ', async () => {
    vi.mocked(baseApi.deviceLogin).mockResolvedValue(loginResult([CAMERA]))
    await show([assignment({ service_count: 1 })])
    await type('field-pin', PIN)

    expect(el('shortcut-notice')).toBeNull()
    expect(el(`assignment-${ACCOUNT}`)).not.toBeNull()
    await click('load-previous')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('device-services'))
    expect(leaveTo).not.toHaveBeenCalled()
  })

  it.each([
    ['通信機が増えた', [assignment({ service_count: 1 }), assignment({ account: OTHER, service_count: 1 })]],
    ['サービスが増えた', [assignment({ service_count: 2 })]],
  ])('読み直した一覧で条件が崩れたら (%s)、近道をやめて今までの表を出す', async (_label, devices) => {
    await showViaShortcut(devices)

    expect(useLandingStore().account).toBeNull()
    expect(el('shortcut-notice')).toBeNull()
    expect(el(`assignment-${ACCOUNT}`)).not.toBeNull()
  })
})
