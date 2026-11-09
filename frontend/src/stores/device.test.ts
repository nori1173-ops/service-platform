import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { baseApi } from '@/services/baseApi'
import type { DeviceLoginResult, DeviceServices, MyDevice } from '@/types/devices'
import { MESSAGES, POLL_INTERVAL_MS, WAIT_LIMIT_MS, useDeviceStore } from './device'

vi.mock('@/services/baseApi', () => ({
  baseApi: {
    getDevices: vi.fn(),
    deviceLogin: vi.fn(),
    startRefresh: vi.fn(),
    getRefreshAvailability: vi.fn(),
    getDeviceServices: vi.fn(),
  },
}))

const meMock = vi.hoisted(() => ({ noteDenied: vi.fn(), load: vi.fn() }))
vi.mock('@/stores/me', () => ({ useMeStore: () => meMock }))

const ACCEPTANCE = '01JBZ0000000000000ACCEPT1'
// 明らかな架空の値。ストアの状態とブラウザの保存領域に残らないことを確かめる
const TOKEN = 'poll-token-for-tests-0123456789abcdefghijklmn'

function started(overrides: Record<string, unknown> = {}) {
  return {
    account: ACCOUNT,
    accepted_at: '2026-11-04T03:00:00Z',
    expires_at: '2026-11-04T03:10:00Z',
    timeout_seconds: 600,
    acceptance_id: ACCEPTANCE,
    poll_token: TOKEN,
    resumed: false,
    ...overrides,
  }
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

const ACCOUNT = '12345678'
const SERIAL = '87654321'
// 明らかな架空の値。ブラウザの保存領域に残らないことを、この文字列が無いことで確かめる
const PIN = '9999'

function assignment(overrides: Partial<MyDevice> = {}): MyDevice {
  return {
    assignment_id: '01JBZ0000000000000ASSIGN1',
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

function loginResult(overrides: Partial<DeviceLoginResult> = {}): DeviceLoginResult {
  return {
    account: ACCOUNT,
    method: 'previous',
    location_name: '見本の現場',
    observation: {},
    devices: [],
    services: [{ id: 'camera', name: 'カメラ画像判定', url: `/camera/?account=${ACCOUNT}` }],
    ...overrides,
  }
}

function deviceServices(overrides: Partial<DeviceServices> = {}): DeviceServices {
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

async function ready(available = true): Promise<ReturnType<typeof useDeviceStore>> {
  vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [assignment()] })
  vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
    account: ACCOUNT,
    available,
    reason: available ? null : 'in_operation',
    in_progress: false,
  })
  const store = useDeviceStore()
  await store.loadDevices()
  await store.select(ACCOUNT)
  store.setPin(PIN)
  return store
}

function storedText(): string {
  const values: string[] = []
  for (const storage of [window.localStorage, window.sessionStorage]) {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index) ?? ''
      values.push(key, storage.getItem(key) ?? '')
    }
  }
  return values.join('\n')
}

describe('useDeviceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  afterEach(() => {
    useDeviceStore().dispose()
    vi.useRealTimers()
  })

  describe('割り当ての一覧と選択', () => {
    it('割り当てられた通信機と誰でも使える通信機に分ける (/me/devices)', async () => {
      vi.mocked(baseApi.getDevices).mockResolvedValue({
        devices: [
          assignment(),
          assignment({ assignment_id: 'g', account: '23456789', basis: 'group', group_label: '会社A > 部署B' }),
          assignment({ assignment_id: null, account: '34567890', basis: 'everyone', serial: null }),
        ],
      })
      const store = useDeviceStore()

      await store.loadDevices()

      expect(store.assignedDevices.map((item) => item.account)).toEqual([ACCOUNT, '23456789'])
      expect(store.everyoneDevices.map((item) => item.account)).toEqual(['34567890'])
      expect(store.loadError).toBeNull()
    })

    it('誰でも使える通信機を選ぶと製造番号の控えが無ければ製造番号を求める', async () => {
      vi.mocked(baseApi.getDevices).mockResolvedValue({
        devices: [assignment({ assignment_id: null, basis: 'everyone', serial: null })],
      })
      vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
        account: ACCOUNT,
        available: true,
        reason: null,
        in_progress: false,
      })
      const store = useDeviceStore()
      await store.loadDevices()

      await store.select(ACCOUNT)

      expect(store.needsSerial).toBe(true)
    })

    it('一覧を読めなければ理由を出す', async () => {
      vi.mocked(baseApi.getDevices).mockRejectedValue(apiError(500, 'X'))
      const store = useDeviceStore()

      await store.loadDevices()

      expect(store.devices).toEqual([])
      expect(store.loadError).toBeTruthy()
    })

    it('選ぶと製造番号の控えが入り、暗証番号は空から始まり、最新値の可否を問い合わせる', async () => {
      const store = await ready()

      expect(store.selected?.account).toBe(ACCOUNT)
      expect(store.serial).toBe(SERIAL)
      expect(store.needsSerial).toBe(false)
      expect(baseApi.getRefreshAvailability).toHaveBeenCalledWith(ACCOUNT)
      expect(store.availability).toMatchObject({ account: ACCOUNT, available: true, loading: false })

      await store.select(ACCOUNT)
      expect(store.pin).toBe('')
    })

    it('製造番号の控えが無ければ製造番号も入力させる', async () => {
      vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [assignment({ serial: null })] })
      vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
        account: ACCOUNT,
        available: true,
        reason: null,
        in_progress: false,
      })
      const store = useDeviceStore()
      await store.loadDevices()

      await store.select(ACCOUNT)

      expect(store.needsSerial).toBe(true)
      expect(store.serial).toBe('')
      store.setPin(PIN)
      expect(store.canLoadPrevious).toBe(false)
      store.setSerial('８７６５４３２１9')
      expect(store.serial).toBe(SERIAL)
      expect(store.canLoadPrevious).toBe(true)
    })

    it('暗証番号は半角数字 4 桁までにそろえる', async () => {
      const store = await ready()

      store.setPin('９a8 77')

      expect(store.pin).toBe('9877')
      store.setPin('12')
      expect(store.canLoadPrevious).toBe(false)
      expect(store.errors.pin).toBeTruthy()
    })

    it('可否を問い合わせられなければ最新値は押せない (fail-closed)', async () => {
      vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [assignment()] })
      vi.mocked(baseApi.getRefreshAvailability).mockRejectedValue(new Error('network'))
      const store = useDeviceStore()
      await store.loadDevices()
      await store.select(ACCOUNT)
      store.setPin(PIN)

      expect(store.canLoadPrevious).toBe(true)
      expect(store.canStartLatest).toBe(false)
      expect(store.refreshBlockedReason).toBe(MESSAGES.guardUnavailable)
    })

    it('運用中は最新値を押せないが、理由の文は出さない', async () => {
      const store = await ready(false)

      expect(store.canStartLatest).toBe(false)
      expect(store.refreshBlockedReason).toBe('')
    })
  })

  describe('前回値を読み込む', () => {
    it('成功すると結果を持ち、暗証番号を捨てる', async () => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockResolvedValue(loginResult())

      await store.loadPrevious()

      expect(baseApi.deviceLogin).toHaveBeenCalledWith({ account: ACCOUNT, serial: SERIAL, pin: PIN, mode: 'previous' })
      expect(store.loggedIn?.account).toBe(ACCOUNT)
      expect(store.pin).toBe('')
      expect(store.phase).toBe('idle')
      expect(store.message).toBeNull()
      expect(storedText()).not.toContain(PIN)
    })

    it.each([
      ['PIN_MISMATCH', 403, 'pinMismatch'],
      ['SERIAL_MISMATCH', 403, 'serialMismatch'],
      ['PIN_LOCKED', 429, 'pinLocked'],
      ['USER_PIN_LOCKED', 429, 'userPinLocked'],
      ['DEVICE_NOT_ASSIGNED', 403, 'notAssigned'],
      ['DEVICE_BACKEND_UNAVAILABLE', 502, 'device_backendError'],
    ] as const)('%s は案内を出し、暗証番号だけを空にする', async (code, status, key) => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(status, code))

      await store.loadPrevious()

      expect(store.message).toEqual({ type: 'error', text: MESSAGES[key] })
      expect(store.pin).toBe('')
      expect(store.serial).toBe(SERIAL)
      expect(store.loggedIn).toBeNull()
      expect(store.phase).toBe('idle')
    })

    it('通信の失敗は通信の案内', async () => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(new Error('Network Error'))

      await store.loadPrevious()

      expect(store.message).toEqual({ type: 'error', text: MESSAGES.networkError })
    })

    it('409 CONFLICT (並行するログイン) は一覧を読み直し、サービスの一覧で確かめてログイン済みなら進む', async () => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(409, 'CONFLICT'))
      const services = deferred<DeviceServices>()
      vi.mocked(baseApi.getDeviceServices).mockReturnValue(services.promise)

      const loading = store.loadPrevious()
      await vi.waitFor(() => expect(baseApi.getDeviceServices).toHaveBeenCalledWith(ACCOUNT))
      expect(store.busy).toBe(true)
      expect(baseApi.getDevices).toHaveBeenCalledTimes(2)
      services.resolve(deviceServices())
      await loading

      expect(store.loggedIn?.account).toBe(ACCOUNT)
      expect(store.message).toBeNull()
      expect(store.phase).toBe('idle')
      expect(store.pin).toBe('')
    })

    it('409 CONFLICT でサービスの一覧が断られたら (ログイン済みでない)、案内を出して止まる', async () => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(409, 'CONFLICT'))
      vi.mocked(baseApi.getDeviceServices).mockRejectedValue(apiError(403, 'DEVICE_LOGIN_REQUIRED'))

      await store.loadPrevious()

      expect(baseApi.getDevices).toHaveBeenCalledTimes(2)
      expect(store.loggedIn).toBeNull()
      expect(store.message).toEqual({ type: 'error', text: 'CONFLICT' })
      expect(store.phase).toBe('idle')
      expect(store.pin).toBe('')
    })
  })

  describe('最新値を取得', () => {
    it('開始は暗証番号を送り、10 秒ごとに受付の acceptance_id と poll_token を付けて照会し、成功で結果を持つ', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue(started())
      vi.mocked(baseApi.deviceLogin)
        .mockRejectedValueOnce(apiError(403, 'PIN_MISMATCH'))
        .mockResolvedValueOnce(loginResult({ method: 'latest' }))

      store.openRefreshWarning()
      expect(store.warningOpen).toBe(true)
      await store.confirmRefresh()

      expect(baseApi.startRefresh).toHaveBeenCalledWith({ account: ACCOUNT, serial: SERIAL, pin: PIN })
      expect(store.phase).toBe('waiting')
      expect(store.pin).toBe(PIN)
      expect(JSON.stringify(store.$state)).not.toContain(TOKEN)

      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      expect(baseApi.deviceLogin).toHaveBeenLastCalledWith({
        account: ACCOUNT,
        serial: SERIAL,
        pin: PIN,
        mode: 'latest',
        acceptance_id: ACCEPTANCE,
        poll_token: TOKEN,
      })
      expect(store.phase).toBe('waiting')
      expect(store.elapsedSeconds).toBe(10)

      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
      expect(store.loggedIn?.method).toBe('latest')
      expect(store.phase).toBe('idle')
      expect(store.pin).toBe('')

      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      expect(baseApi.deviceLogin).toHaveBeenCalledTimes(2)
      expect(storedText()).not.toContain(TOKEN)
    })

    it('照会の間隔が短いと断られても待ち続ける', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue(started())
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(429, 'POLL_TOO_SOON'))

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2)

      expect(store.phase).toBe('waiting')
      expect(baseApi.deviceLogin).toHaveBeenCalledTimes(2)
    })

    it.each(['USER_SUSPENDED', 'USER_NOT_REGISTERED', 'ADMIN_NOT_ACTIVE'])(
      '待機中の %s は待機を終え、暗証番号を捨て、状態を読み直してアカウントの画面へ案内する',
      async (code) => {
        vi.useFakeTimers()
        const store = await ready()
        vi.mocked(baseApi.startRefresh).mockResolvedValue(started())
        vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, code))

        await store.confirmRefresh()
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

        expect(store.phase).toBe('idle')
        expect(store.pin).toBe('')
        expect(store.leaveTo).toBe('account')
        expect(meMock.noteDenied).toHaveBeenCalledWith(code)
        expect(meMock.load).toHaveBeenCalled()
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
        expect(baseApi.deviceLogin).toHaveBeenCalledTimes(1)
      },
    )

    it('待機中の割り当ての終了は待機を終え、割り当ての一覧を読み直す', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue(started())
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'DEVICE_NOT_ASSIGNED'))
      vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [] })

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

      expect(store.phase).toBe('idle')
      expect(store.pin).toBe('')
      expect(store.message).toEqual({ type: 'error', text: MESSAGES.notAssigned })
      expect(baseApi.getDevices).toHaveBeenCalledTimes(2)
      expect(store.selected).toBeNull()
    })

    it('暗証番号違いの 403 は待ち続ける (利用者の状態の 403 と区別する)', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue(started())
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'PIN_MISMATCH'))

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2)

      expect(store.phase).toBe('waiting')
      expect(store.leaveTo).toBeNull()
      expect(meMock.noteDenied).not.toHaveBeenCalled()
    })

    it('10 分で打ち切り、暗証番号を捨てる', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue({} as never)
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'PIN_MISMATCH'))

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(WAIT_LIMIT_MS + POLL_INTERVAL_MS)

      expect(store.phase).toBe('idle')
      expect(store.message).toEqual({ type: 'warning', text: MESSAGES.waitTimedOut })
      expect(store.pin).toBe('')
      const calls = vi.mocked(baseApi.deviceLogin).mock.calls.length
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
      expect(baseApi.deviceLogin).toHaveBeenCalledTimes(calls)
    })

    it('待機を中断すると照会をやめ、暗証番号を捨てる', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue({} as never)

      await store.confirmRefresh()
      store.cancelWaiting()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)

      expect(baseApi.deviceLogin).not.toHaveBeenCalled()
      expect(store.message).toEqual({ type: 'info', text: MESSAGES.waitCancelled })
      expect(store.pin).toBe('')
      expect(store.phase).toBe('idle')
    })

    it.each([
      ['SERIAL_MISMATCH', 403, 'serialMismatch'],
      ['PIN_LOCKED', 429, 'pinLocked'],
      ['USER_PIN_LOCKED', 429, 'userPinLocked'],
      ['DEVICE_NOT_ASSIGNED', 403, 'notAssigned'],
    ] as const)('待機中の %s は待機を終える', async (code, status, key) => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue({} as never)
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(status, code))

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

      expect(store.phase).toBe('idle')
      expect(store.message).toEqual({ type: 'error', text: MESSAGES[key] })
      expect(store.pin).toBe('')
    })

    it('自分の実行中の取得があれば、新しい poll_token で待機を再開する', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockResolvedValue(started({ resumed: true }))

      await store.confirmRefresh()

      expect(store.phase).toBe('waiting')
      expect(store.message).toEqual({ type: 'info', text: MESSAGES.inProgress })
    })

    it('ほかの方の実行中の取得があれば待たずに案内する', async () => {
      vi.useFakeTimers()
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockRejectedValue(apiError(409, 'IN_PROGRESS'))

      await store.confirmRefresh()
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2)

      expect(store.phase).toBe('idle')
      expect(store.message).toEqual({ type: 'info', text: MESSAGES.inProgressOther })
      expect(baseApi.deviceLogin).not.toHaveBeenCalled()
    })

    it('開始の時点で運用中なら押せなくするだけ', async () => {
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockRejectedValue(apiError(409, 'IN_OPERATION'))

      await store.confirmRefresh()

      expect(store.phase).toBe('idle')
      expect(store.canStartLatest).toBe(false)
      expect(store.message).toBeNull()
    })

    it.each([
      ['NO_RESPONSE', 404, 'noResponse'],
      ['GUARD_UNAVAILABLE', 503, 'guardUnavailable'],
      ['SERIAL_MISMATCH', 403, 'serialMismatch'],
      ['PIN_MISMATCH', 403, 'pinMismatch'],
      ['NO_PREVIOUS_SETTINGS', 409, 'noPreviousSettings'],
      ['PIN_LOCKED', 429, 'pinLocked'],
    ] as const)('開始の %s は案内を出して暗証番号を空にする', async (code, status, key) => {
      const store = await ready()
      vi.mocked(baseApi.startRefresh).mockRejectedValue(apiError(status, code))

      await store.confirmRefresh()

      expect(store.phase).toBe('idle')
      expect(store.message).toEqual({ type: 'error', text: MESSAGES[key] })
      expect(store.pin).toBe('')
    })

    it('押せないときは開始しない', async () => {
      const store = await ready(false)

      store.openRefreshWarning()
      await store.confirmRefresh()

      expect(store.warningOpen).toBe(false)
      expect(baseApi.startRefresh).not.toHaveBeenCalled()
    })
  })

  describe('画面を離れた後の遅れた応答', () => {
    it('前回値の照会の応答が離れた後に返っても、結果を持たず遷移を起こさない', async () => {
      const store = await ready()
      const pending = deferred<DeviceLoginResult>()
      vi.mocked(baseApi.deviceLogin).mockReturnValue(pending.promise)

      const running = store.loadPrevious()
      store.dispose()
      pending.resolve(loginResult())
      await running

      expect(store.loggedIn).toBeNull()
      expect(store.phase).toBe('idle')
      expect(store.pin).toBe('')
    })

    it('最新値の開始の応答が離れた後に返っても、待機を始めない', async () => {
      vi.useFakeTimers()
      const store = await ready()
      const pending = deferred<ReturnType<typeof started>>()
      vi.mocked(baseApi.startRefresh).mockReturnValue(pending.promise as never)

      const running = store.confirmRefresh()
      store.dispose()
      pending.resolve(started())
      await running
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)

      expect(store.phase).toBe('idle')
      expect(baseApi.deviceLogin).not.toHaveBeenCalled()
    })

    it('最新値の開始の失敗が離れた後に返っても、案内を出さない', async () => {
      const store = await ready()
      let reject!: (reason: unknown) => void
      vi.mocked(baseApi.startRefresh).mockReturnValue(
        new Promise((_resolve, fail) => {
          reject = fail
        }),
      )

      const running = store.confirmRefresh()
      store.dispose()
      reject(apiError(403, 'PIN_MISMATCH'))
      await running

      expect(store.message).toBeNull()
      expect(store.phase).toBe('idle')
    })
  })

  describe('利用者の切り替えと一覧の取り直し', () => {
    it('reset で選択・入力・一覧・案内を全部捨てる', async () => {
      const store = await ready()
      store.message = { type: 'error', text: 'x' }

      store.reset()

      expect(store.selected).toBeNull()
      expect(store.serial).toBe('')
      expect(store.pin).toBe('')
      expect(store.devices).toEqual([])
      expect(store.message).toBeNull()
      expect(store.loggedIn).toBeNull()
      expect(store.availability.account).toBe('')
    })

    it('取り直しで選択を assignment_id で照合し、製造番号の控えを反映する', async () => {
      vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [assignment({ serial: null })] })
      vi.mocked(baseApi.getRefreshAvailability).mockResolvedValue({
        account: ACCOUNT,
        available: true,
        reason: null,
        in_progress: false,
      })
      const store = useDeviceStore()
      await store.loadDevices()
      await store.select(ACCOUNT)
      expect(store.needsSerial).toBe(true)

      vi.mocked(baseApi.getDevices).mockResolvedValue({ devices: [assignment()] })
      await store.loadDevices()

      expect(store.selected?.serial).toBe(SERIAL)
      expect(store.needsSerial).toBe(false)
      expect(store.serial).toBe(SERIAL)
    })

    it('取り直しで選んでいた割り当てが無くなれば選択を捨てる (同じ通信機の付け直しでも)', async () => {
      const store = await ready()

      vi.mocked(baseApi.getDevices).mockResolvedValue({
        devices: [assignment({ assignment_id: '01JBZ0000000000000ASSIGN2' })],
      })
      await store.loadDevices()

      expect(store.selected).toBeNull()
      expect(store.serial).toBe('')
      expect(store.pin).toBe('')
    })

    it('reset の後に旧い一覧の取得が失敗しても、新しい一覧と状態を消さない', async () => {
      const store = useDeviceStore()
      let rejectOld!: (reason: unknown) => void
      vi.mocked(baseApi.getDevices)
        .mockReturnValueOnce(
          new Promise((_resolve, reject) => {
            rejectOld = reject
          }),
        )
        .mockResolvedValueOnce({ devices: [assignment()] })

      const old = store.loadDevices()
      store.reset()
      await store.loadDevices()
      rejectOld(apiError(500, 'X'))
      await old

      expect(store.devices.map((item) => item.account)).toEqual([ACCOUNT])
      expect(store.loadError).toBeNull()
      expect(store.loading).toBe(false)
    })

    it('reset で読み込み中の表示も消す', async () => {
      const store = useDeviceStore()
      vi.mocked(baseApi.getDevices).mockReturnValue(new Promise(() => {}))

      void store.loadDevices()
      expect(store.loading).toBe(true)
      store.reset()

      expect(store.loading).toBe(false)
    })

    it('前回値の照会で利用者の状態の 403 を受けたら、アカウントの画面へ案内する', async () => {
      const store = await ready()
      vi.mocked(baseApi.deviceLogin).mockRejectedValue(apiError(403, 'USER_SUSPENDED'))

      await store.loadPrevious()

      expect(store.leaveTo).toBe('account')
      expect(store.pin).toBe('')
      expect(meMock.noteDenied).toHaveBeenCalledWith('USER_SUSPENDED')
    })
  })

  it('dispose で待機を止め、暗証番号を捨てる (画面を離れたとき)', async () => {
    vi.useFakeTimers()
    const store = await ready()
    vi.mocked(baseApi.startRefresh).mockResolvedValue({} as never)
    await store.confirmRefresh()

    store.dispose()
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)

    expect(store.pin).toBe('')
    expect(store.phase).toBe('idle')
    expect(baseApi.deviceLogin).not.toHaveBeenCalled()
  })

  it('consumeLogin は結果を 1 回だけ渡す', async () => {
    const store = await ready()
    vi.mocked(baseApi.deviceLogin).mockResolvedValue(loginResult())
    await store.loadPrevious()

    expect(store.consumeLogin()?.account).toBe(ACCOUNT)
    expect(store.consumeLogin()).toBeNull()
  })
})
