import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { adminUsersApi } from '../api'
import { useDevicePeriodsStore } from '../stores/devicePeriods'
import { PID, UID, AID, GID, START, period, apiError, deferred } from './periodFixtures'
import type { DevicePeriodDetail, DevicePeriodsResponse } from '../types'

vi.mock('../api', () => ({ adminUsersApi: {
  listDevicePeriods: vi.fn(), getDevicePeriod: vi.fn(), startDevicePeriod: vi.fn(),
  endDevicePeriod: vi.fn(), assign: vi.fn(), endAssignment: vi.fn(), grant: vi.fn(), endGrant: vi.fn(),
} }))
beforeEach(() => {
  setActivePinia(createPinia()); vi.resetAllMocks()
  vi.mocked(adminUsersApi.listDevicePeriods).mockResolvedValue({ device_periods: [period()] })
  vi.mocked(adminUsersApi.getDevicePeriod).mockImplementation(async (id) => period({ device_period_id: id }))
})
describe('利用期間のストア', () => {
  it('一覧は詳細から割り当てと許可を補い、切り替えをAPIへ送る', async () => {
    const store = useDevicePeriodsStore()
    await store.fetch('all')
    expect(adminUsersApi.listDevicePeriods).toHaveBeenCalledWith('all')
    expect(store.periods[0]).toMatchObject({ assignments: [{ assignment_id: AID }], grants: [{ service: 'camera' }] })
    expect(store.loading).toBe(false)
  })
  it('詳細の取得に失敗したらゼロ件として表示しない', async () => {
    vi.mocked(adminUsersApi.getDevicePeriod).mockRejectedValue(new Error('offline'))
    const store = useDevicePeriodsStore(); await store.fetch()
    expect(store.error).toBeTruthy(); expect(store.periods).toEqual([])
  })
  it('一覧の失敗は文言を表示する', async () => {
    vi.mocked(adminUsersApi.listDevicePeriods).mockRejectedValue(apiError('ADMIN_NOT_ACTIVE'))
    const store = useDevicePeriodsStore(); await store.fetch()
    expect(store.error).toBe('server message')
  })
  it('遅れた一覧で最後の切り替えを上書きしない', async () => {
    const first = deferred<DevicePeriodsResponse>()
    vi.mocked(adminUsersApi.listDevicePeriods).mockReturnValueOnce(first.promise)
    const store = useDevicePeriodsStore(); const pending = store.fetch('active')
    await store.fetch('all'); first.resolve({ device_periods: [] }); await pending
    expect(store.periods).toHaveLength(1); expect(store.status).toBe('all')
  })
  it('切り替えの開始時に古い一覧を捨て、古い失敗を表示しない', async () => {
    const store = useDevicePeriodsStore(); await store.fetch()
    const old = deferred<DevicePeriodsResponse>()
    vi.mocked(adminUsersApi.listDevicePeriods).mockReturnValueOnce(old.promise)
    const pending = store.fetch('all'); expect(store.periods).toEqual([])
    await store.fetch('active'); old.reject(new Error('old')); await pending
    expect(store.error).toBeNull(); expect(store.periods).toHaveLength(1)
  })
  it('別の詳細を読む間は古い詳細を捨て、遅れた応答を捨てる', async () => {
    const store = useDevicePeriodsStore(); await store.fetchDetail(PID)
    const old = deferred<DevicePeriodDetail>()
    vi.mocked(adminUsersApi.getDevicePeriod).mockReturnValueOnce(old.promise)
    const pending = store.fetchDetail('old'); expect(store.detail).toBeNull()
    await store.fetchDetail('next'); old.resolve(period()); await pending
    expect(store.detail?.device_period_id).toBe('next'); expect(store.detailLoading).toBe(false)
  })
  it('同じ詳細は再取得中も残し、失敗時は破棄する', async () => {
    const store = useDevicePeriodsStore(); await store.fetchDetail(PID)
    const next = deferred<DevicePeriodDetail>()
    vi.mocked(adminUsersApi.getDevicePeriod).mockReturnValueOnce(next.promise)
    const pending = store.fetchDetail(PID); expect(store.detail?.device_period_id).toBe(PID)
    next.reject(new Error('offline')); await pending
    expect(store.detail).toBeNull(); expect(store.detailError).toBeTruthy()
  })
  it('開始後に一覧を再取得する', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockResolvedValue(period())
    const store = useDevicePeriodsStore()
    expect((await store.start({ account: '00000001', org_name: '組織A', access: 'everyone' })).ok).toBe(true)
    expect(adminUsersApi.listDevicePeriods).toHaveBeenCalled()
  })
  it('開始の成功は一覧の読み直しを待たずに返す', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockResolvedValue(period())
    vi.mocked(adminUsersApi.listDevicePeriods).mockReturnValue(new Promise(() => {}))
    const store = useDevicePeriodsStore()
    const outcome = await Promise.race([
      store.start({ account: '00000001', org_name: '組織A', access: 'everyone' }),
      new Promise((resolve) => setTimeout(() => resolve('pending'), 50)),
    ])
    expect(outcome).toMatchObject({ ok: true })
    expect(store.working).toBe(false)
    expect(adminUsersApi.listDevicePeriods).toHaveBeenCalled()
  })
  describe('一覧の行ごとの詳細の取得', () => {
    const ids = ['P0', 'P1', 'P2', 'P3', 'P4', 'P5']
    let pending: Map<string, ReturnType<typeof deferred<DevicePeriodDetail>>>
    beforeEach(() => {
      pending = new Map()
      vi.mocked(adminUsersApi.listDevicePeriods).mockResolvedValue({
        device_periods: ids.map((id) => period({ device_period_id: id })),
      })
      vi.mocked(adminUsersApi.getDevicePeriod).mockImplementation((id) => {
        const next = deferred<DevicePeriodDetail>()
        pending.set(id, next)
        return next.promise
      })
    })
    it('同時に 4 件まで読み、全件を補う', async () => {
      const store = useDevicePeriodsStore()
      const done = store.fetch('all'); await flushPromises()
      expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(4)
      let settled = 0
      for (const id of ids) {
        pending.get(id)!.resolve(period({ device_period_id: id })); settled += 1; await flushPromises()
        // 始めた数 - 終わった数 = 同時に待っている数
        expect(pending.size - settled).toBeLessThanOrEqual(4)
      }
      await done
      expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(6)
      expect(store.periods.map((row) => row.device_period_id)).toEqual(ids)
    })
    it('1 件でも失敗したら、まだ始めていない取得をしない', async () => {
      const store = useDevicePeriodsStore()
      const done = store.fetch('all'); await flushPromises()
      expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(4)
      pending.get('P0')!.reject(new Error('offline')); await flushPromises()
      for (const id of ['P1', 'P2', 'P3']) {
        pending.get(id)!.resolve(period({ device_period_id: id })); await flushPromises()
      }
      await done
      expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(4)
      expect(store.error).toBeTruthy(); expect(store.periods).toEqual([]); expect(store.loading).toBe(false)
    })
  })
  it.each([
    ['endDevicePeriod', 'end', [PID]],
    ['assign', 'assign', [PID, { user_id: UID, valid_from: START, valid_to: null }]],
    ['endAssignment', 'endAssignment', [PID, AID]],
    ['grant', 'grant', [PID, { service: 'camera', starts_at: START, ends_at: null }]],
    ['endGrant', 'endGrant', [PID, GID]],
  ] as const)('%s の後は詳細を読み直す', async (endpoint, method, args) => {
    vi.mocked(adminUsersApi[endpoint]).mockResolvedValue(period() as never)
    const store = useDevicePeriodsStore(); await store.fetchDetail(PID)
    // 可変引数の操作を同じ表で検査する
    const call = store[method] as (...args: unknown[]) => Promise<{ ok: boolean }>
    expect((await call(...args)).ok).toBe(true)
    expect(adminUsersApi[endpoint]).toHaveBeenCalledWith(...args)
    expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(2)
  })
  it.each([
    ['DEVICE_PERIOD_ACTIVE', '稼働中の利用期間'], ['ASSIGNMENT_OVERLAP', '割り当ての期間が重な'],
    ['GRANT_OVERLAP', '許可の期間が重な'], ['CONFLICT', '詳細を読み直してからやり直して'],
    ['DEVICE_PERIOD_ENDED', '終了'], ['ASSIGNMENT_ENDED', '終了'], ['GRANT_ENDED', '終了'],
    ['TRANSACTION_LIMIT_EXCEEDED', '先に割り当てや許可'],
  ])('%s を日本語で案内し、失敗後も詳細を読む', async (code, message) => {
    vi.mocked(adminUsersApi.endDevicePeriod).mockRejectedValue(apiError(code))
    const store = useDevicePeriodsStore(); await store.fetchDetail(PID)
    const result = await store.end(PID)
    expect(result).toMatchObject({ ok: false, code, message: expect.stringContaining(message) })
    expect(adminUsersApi.getDevicePeriod).toHaveBeenCalledTimes(2)
  })
  it('通信失敗を案内する', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockRejectedValue(new Error('offline'))
    expect(await useDevicePeriodsStore().start({ account: '00000001', org_name: '組織A', access: 'everyone' }))
      .toMatchObject({ ok: false, code: null, message: expect.stringContaining('できませんでした') })
  })
  it('操作中に別の詳細を開いたら、操作後の再取得で元へ戻さない', async () => {
    const operation = deferred<DevicePeriodDetail>()
    vi.mocked(adminUsersApi.endDevicePeriod).mockReturnValueOnce(operation.promise)
    const store = useDevicePeriodsStore(); await store.fetchDetail(PID)
    const pending = store.end(PID); expect(store.working).toBe(true)
    await store.fetchDetail('next'); operation.resolve(period()); await pending
    expect(store.detail?.device_period_id).toBe('next'); expect(store.working).toBe(false)
  })
})
