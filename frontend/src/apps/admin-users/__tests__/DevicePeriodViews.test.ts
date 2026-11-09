import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VAutocomplete } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import { adminUsersApi } from '../api'
import AssignDialog from '../components/AssignDialog.vue'
import GrantDialog from '../components/GrantDialog.vue'
import { useDevicePeriodsStore } from '../stores/devicePeriods'
import DevicePeriodsView from '../views/DevicePeriodsView.vue'
import DevicePeriodDetailView from '../views/DevicePeriodDetailView.vue'
import { PID, UID, AID, GID, START, period, user, deferred, apiError } from './periodFixtures'
import type { DevicePeriodDetail } from '../types'

vi.mock('../api', () => ({ adminUsersApi: {
  listUsers: vi.fn(), listDevicePeriods: vi.fn(), getDevicePeriod: vi.fn(), startDevicePeriod: vi.fn(),
  endDevicePeriod: vi.fn(), assign: vi.fn(), endAssignment: vi.fn(), grant: vi.fn(), endGrant: vi.fn(),
} }))
let wrapper: VueWrapper
let router: Router
const root = '/admin-users/device-periods'
const PAST = '2026-01-01T00:00:00Z'
function el(id: string): HTMLElement { return document.querySelector(`[data-test="${id}"]`)! }
async function click(id: string): Promise<void> { el(id).click(); await flushPromises() }
async function type(id: string, value: string): Promise<void> {
  const input = el(id).querySelector('input,textarea') as HTMLInputElement
  input.value = value; input.dispatchEvent(new Event('input')); await flushPromises()
}
async function show(path = `${root}/${PID}`): Promise<void> {
  router = createRouter({ history: createMemoryHistory(), routes: [
    { path: root, name: 'admin-users.device-periods', component: DevicePeriodsView },
    { path: `${root}/:id`, name: 'admin-users.device-period', component: DevicePeriodDetailView, props: true },
    { path: '/admin-users/users/:userId', name: 'admin-users.user', component: { template: '<div />' } },
  ] })
  await router.push(path); await router.isReady()
  wrapper = mount({ template: '<RouterView v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></RouterView>' },
    { global: { plugins: [createVuetify({ components, directives }), router] }, attachTo: document.body })
  await flushPromises()
}
beforeEach(() => {
  setActivePinia(createPinia()); vi.resetAllMocks()
  vi.mocked(adminUsersApi.listUsers).mockResolvedValue({ users: [user()], self_user_id: UID })
  vi.mocked(adminUsersApi.listDevicePeriods).mockResolvedValue({ device_periods: [period()] })
  vi.mocked(adminUsersApi.getDevicePeriod).mockImplementation(async (id) => period({ device_period_id: id }))
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
describe('利用期間の一覧', () => {
  it('列とJSTを表示し、全部へ切り替える', async () => {
    await show(root)
    expect(wrapper.text()).toContain('通信機アカウント'); expect(wrapper.text()).toContain('組織A')
    expect(wrapper.text()).toContain('10:00'); expect(wrapper.text()).toContain('カメラ画像判定')
    expect(el(`period-${PID}`).textContent).toContain('1')
    await click('filter-all'); expect(adminUsersApi.listDevicePeriods).toHaveBeenLastCalledWith('all')
    // 画面にある VDialog (VOverlay) が router.beforeEach を setTimeout で遅らせるので、移るのを待つ
    await click(`period-link-${PID}`); await vi.waitFor(() => expect(router.currentRoute.value.params.id).toBe(PID))
  })
  it('割り当ては開いている数と全体の数、許可は今有効なサービスを出す', async () => {
    const data = period()
    data.assignments.push({ ...data.assignments[0], assignment_id: 'closed', valid_to: PAST })
    data.grants[0] = { ...data.grants[0], status: 'ended' }
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(data)
    await show(root)
    expect(el(`period-assignments-${PID}`).textContent).toContain('1 件')
    expect(el(`period-assignments-${PID}`).textContent).toContain('全 2 件')
    expect(el(`period-grants-${PID}`).textContent).not.toContain('カメラ画像判定')
  })
  it('開始前の許可は今有効なサービスに出さない', async () => {
    const data = period()
    data.grants[0] = { ...data.grants[0], starts_at: '2999-01-01T00:00:00Z' }
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(data)
    await show(root)
    expect(el(`period-grants-${PID}`).textContent).not.toContain('カメラ画像判定')
  })
  it('終了した利用期間は状態を終了として薄く出す', async () => {
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(period({ status: 'ended', ended_at: START }))
    await show(root); await click('filter-all')
    expect(el(`period-${PID}`).textContent).toContain('終了')
    expect(el(`period-${PID}`).classList.contains('text-disabled')).toBe(true)
  })
  it('読み込み中に開始を止める', async () => {
    vi.mocked(adminUsersApi.listDevicePeriods).mockReturnValue(new Promise(() => {}))
    await show(root); expect(el('start-period').hasAttribute('disabled')).toBe(true)
  })
  it('開始の失敗をダイアログで案内する', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockRejectedValue(apiError('DEVICE_PERIOD_ACTIVE'))
    await show(root); await click('start-period')
    for (const [id, value] of [['period-account', '00000001'], ['period-org', '組織A']]) {
      const input = el(id).querySelector('input')!; input.value = value; input.dispatchEvent(new Event('input'))
    }
    await click('period-submit'); expect(document.body.textContent).toContain('稼働中の利用期間')
    expect(el('period-submit')).toBeTruthy()
  })
  it('開始したら知らせて、その利用期間の詳細へ移る', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockResolvedValue(period({ device_period_id: 'NEW' }))
    await show(root); await click('start-period')
    await type('period-account', '00000001'); await type('period-org', '組織A'); await click('period-submit')
    expect(adminUsersApi.startDevicePeriod).toHaveBeenCalledWith({ account: '00000001', org_name: '組織A', access: 'everyone' })
    expect(useUiStore().snackbar.color).toBe('success')
    await vi.waitFor(() => expect(router.currentRoute.value.params.id).toBe('NEW'))
  })
  it('開始の成功は一覧の読み直しの応答を待たずに知らせて移る', async () => {
    vi.mocked(adminUsersApi.startDevicePeriod).mockResolvedValue(period({ device_period_id: 'NEW' }))
    await show(root)
    vi.mocked(adminUsersApi.listDevicePeriods).mockReturnValue(new Promise(() => {}))
    await click('start-period')
    await type('period-account', '00000001'); await type('period-org', '組織A'); await click('period-submit')
    expect(useUiStore().snackbar.color).toBe('success')
    await vi.waitFor(() => expect(router.currentRoute.value.params.id).toBe('NEW'))
  })
  it('空の一覧と取得失敗を区別して表示する', async () => {
    vi.mocked(adminUsersApi.listDevicePeriods).mockResolvedValue({ device_periods: [] })
    await show(root); expect(wrapper.text()).toContain('利用期間はありません')
    vi.mocked(adminUsersApi.listDevicePeriods).mockRejectedValue(new Error('offline'))
    await click('reload-periods'); expect(el('periods-error')).toBeTruthy()
    expect(wrapper.text()).not.toContain('利用期間はありません')
  })
})
describe('利用期間の詳細', () => {
  it('利用者と製造番号・期間・許可を表示する', async () => {
    await show(); expect(wrapper.text()).toContain('user@example.com'); expect(wrapper.text()).toContain('SN-001')
    expect(wrapper.text()).toContain('カメラ画像判定'); expect(wrapper.text()).toContain('10:00')
    expect(wrapper.text()).toContain('利用期間の終わりまで')
  })
  it('利用者から利用者の詳細へ移れる。一覧に無い利用者は user_id を出す', async () => {
    const data = period()
    data.assignments.push({ ...data.assignments[0], assignment_id: 'other', user_id: 'missing-user' })
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(data)
    await show()
    expect(el(`assignment-user-${AID}`).getAttribute('href')).toBe(`/admin-users/users/${UID}`)
    expect(el('assignment-user-other').textContent).toContain('missing-user')
  })
  it('終了済みの履歴を残して薄くし、終了ボタンを消す', async () => {
    const data = period(); data.assignments[0].valid_to = START; data.grants[0].status = 'ended'
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(data)
    await show(); expect(el(`assignment-${AID}`).classList.contains('text-disabled')).toBe(true)
    expect(el(`grant-${GID}`).classList.contains('text-disabled')).toBe(true)
    expect(el(`end-assignment-${AID}`)).toBeNull(); expect(el(`end-grant-${GID}`)).toBeNull()
  })
  it.each([
    ['end-assignment-', AID, 'endAssignment'], ['end-grant-', GID, 'endGrant'],
  ] as const)('確認した対象だけを終了する %s', async (prefix, id, endpoint) => {
    vi.mocked(adminUsersApi[endpoint]).mockResolvedValue({} as never)
    await show(); await click(`${prefix}${id}`)
    expect(adminUsersApi[endpoint]).not.toHaveBeenCalled()
    await click('confirm-ok'); expect(adminUsersApi[endpoint]).toHaveBeenCalledWith(PID, id)
    expect(useUiStore().snackbar.color).toBe('success')
  })
  it('割り当てる: 一覧から利用者を選び、JST の入力をタイムゾーン付きで送る', async () => {
    vi.mocked(adminUsersApi.assign).mockResolvedValue({} as never)
    await show(); await click('assign-open')
    wrapper.findComponent(VAutocomplete).vm.$emit('update:modelValue', UID); await flushPromises()
    await type('assign-from', '2026-11-04T12:00'); await type('assign-to', '2026-11-05T12:00')
    await click('assign-submit')
    expect(adminUsersApi.assign).toHaveBeenCalledWith(PID, {
      target_type: 'user', user_id: UID, valid_from: '2026-11-04T12:00:00+09:00', valid_to: '2026-11-05T12:00:00+09:00',
    })
    expect(useUiStore().snackbar.color).toBe('success')
    expect(wrapper.findComponent(AssignDialog).props('modelValue')).toBe(false)
  })
  it('割り当ての 409 はダイアログを閉じずに案内する', async () => {
    vi.mocked(adminUsersApi.assign).mockRejectedValue(apiError('ASSIGNMENT_OVERLAP'))
    await show(); await click('assign-open')
    wrapper.findComponent(VAutocomplete).vm.$emit('update:modelValue', UID); await flushPromises()
    await type('assign-from', '2026-11-04T12:00'); await click('assign-submit')
    expect(el('dialog-error').textContent).toContain('割り当ての期間が重なっています')
    expect(wrapper.findComponent(AssignDialog).props('modelValue')).toBe(true)
  })
  it('許可を出す: camera と期間を送る。409 はダイアログで案内する', async () => {
    vi.mocked(adminUsersApi.grant).mockRejectedValueOnce(apiError('GRANT_OVERLAP')).mockResolvedValueOnce({} as never)
    await show(); await click('grant-open')
    await type('grant-from', '2026-11-04T12:00'); await click('grant-submit')
    expect(el('dialog-error').textContent).toContain('許可の期間が重なっています')
    await click('grant-submit')
    expect(adminUsersApi.grant).toHaveBeenLastCalledWith(PID, {
      service: 'camera', starts_at: '2026-11-04T12:00:00+09:00', ends_at: null,
    })
    expect(useUiStore().snackbar.color).toBe('success')
    expect(wrapper.findComponent(GrantDialog).props('modelValue')).toBe(false)
  })
  it('利用期間を終える: 引き渡しの手順を出し、確かめてから送る', async () => {
    vi.mocked(adminUsersApi.endDevicePeriod).mockResolvedValue(period({ status: 'ended' }))
    await show(); await click('end-period')
    expect(el('handover-steps').textContent).toContain('暗証番号')
    expect(adminUsersApi.endDevicePeriod).not.toHaveBeenCalled()
    await click('confirm-ok'); expect(adminUsersApi.endDevicePeriod).toHaveBeenCalledWith(PID)
    expect(useUiStore().snackbar.color).toBe('success')
  })
  it('確認後にURLが変わったら元の操作を送らない', async () => {
    await show(); await click(`end-assignment-${AID}`)
    const oldButton = el('confirm-ok')
    await router.push(`${root}/next`); await flushPromises(); oldButton.click(); await flushPromises()
    expect(adminUsersApi.endAssignment).not.toHaveBeenCalled()
  })
  it('割り当てのダイアログを開いた後にURLが変わったら送らない', async () => {
    await show(); await click('assign-open')
    wrapper.findComponent(VAutocomplete).vm.$emit('update:modelValue', UID); await flushPromises()
    const dialog = wrapper.findComponent(AssignDialog)
    await router.push(`${root}/next`); await flushPromises()
    dialog.vm.$emit('submit', { user_id: UID, valid_from: '2026-11-04T12:00:00+09:00', valid_to: null })
    await flushPromises()
    expect(adminUsersApi.assign).not.toHaveBeenCalled()
  })
  it('同じURLでも確認時の対象ラベルを固定する', async () => {
    await show(); await click('end-period')
    useDevicePeriodsStore().detail = period({ account: '00000002', org_name: '組織B' }); await flushPromises()
    expect(el('confirm-message').textContent).toContain('00000001')
    expect(el('confirm-message').textContent).not.toContain('00000002')
  })
  it('再取得中は開いているダイアログからも操作できない', async () => {
    await show(); await click('end-period')
    vi.mocked(adminUsersApi.getDevicePeriod).mockReturnValue(new Promise(() => {}))
    void useDevicePeriodsStore().fetchDetail(PID); await flushPromises()
    await click('confirm-ok'); expect(adminUsersApi.endDevicePeriod).not.toHaveBeenCalled()
  })
  it('終了中の二重送信を止める', async () => {
    const pending = deferred<DevicePeriodDetail>()
    vi.mocked(adminUsersApi.endDevicePeriod).mockReturnValue(pending.promise)
    await show(); await click('end-period'); await click('confirm-ok'); await click('confirm-ok')
    expect(adminUsersApi.endDevicePeriod).toHaveBeenCalledTimes(1)
    expect(el('assign-open').hasAttribute('disabled')).toBe(true)
    pending.resolve(period({ status: 'ended' })); await flushPromises()
  })
  it('409は通知し、詳細の再取得に失敗した場合は操作を出さない', async () => {
    vi.mocked(adminUsersApi.endDevicePeriod).mockRejectedValue(apiError('CONFLICT'))
    await show(); await click('end-period')
    vi.mocked(adminUsersApi.getDevicePeriod).mockRejectedValue(new Error('offline'))
    await click('confirm-ok'); expect(useUiStore().snackbar.text).toContain('詳細を読み直してから')
    expect(el('detail-error')).toBeTruthy(); expect(el('end-period')).toBeNull()
  })
  it('利用者一覧の取得失敗を表示し、割り当てを止める', async () => {
    vi.mocked(adminUsersApi.listUsers).mockRejectedValue(new Error('offline'))
    await show(); expect(el('users-error')).toBeTruthy(); expect(el('assign-open').hasAttribute('disabled')).toBe(true)
  })
  it('終了した利用期間では追加と終了を出さない', async () => {
    vi.mocked(adminUsersApi.getDevicePeriod).mockResolvedValue(period({ status: 'ended', ended_at: START }))
    await show(); expect(wrapper.text()).toContain('終了')
    expect(el('assign-open')).toBeNull(); expect(el('grant-open')).toBeNull(); expect(el('end-period')).toBeNull()
  })
})
