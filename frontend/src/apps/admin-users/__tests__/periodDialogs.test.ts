import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VAutocomplete } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Component } from 'vue'
import StartDevicePeriodDialog from '../components/StartDevicePeriodDialog.vue'
import AssignDialog from '../components/AssignDialog.vue'
import GrantDialog from '../components/GrantDialog.vue'
import EndConfirmDialog from '../components/EndConfirmDialog.vue'
import { START, UID, user } from './periodFixtures'

let wrapper: VueWrapper
async function open(component: Component, props: Record<string, unknown> = {}): Promise<VueWrapper> {
  wrapper = mount(component, { props: { modelValue: true, ...props },
    global: { plugins: [createVuetify({ components, directives })] }, attachTo: document.body })
  await flushPromises(); return wrapper
}
function el(id: string): HTMLElement {
  const found = document.querySelector(`[data-test="${id}"]`) as HTMLElement | null
  if (!found) throw new Error(`[data-test="${id}"] が見つかりません`)
  return found
}
async function type(id: string, value: string): Promise<void> {
  const input = el(id).querySelector('input,textarea') as HTMLInputElement
  input.value = value; input.dispatchEvent(new Event('input')); await flushPromises()
}
async function click(id: string): Promise<void> { el(id).click(); await flushPromises() }
function choiceLabels(): string[] {
  const props = wrapper.findComponent(VAutocomplete).props() as unknown as { items: { label: string }[] }
  return props.items.map((item) => item.label)
}
async function pick(userId: string): Promise<void> {
  wrapper.findComponent(VAutocomplete).vm.$emit('update:modelValue', userId); await flushPromises()
}
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
describe('利用期間を始める', () => {
  it.each(['1234567', '123456789', '１２３４５６７８', '1234x678'])('8桁以外 %s は送らない', async (account) => {
    await open(StartDevicePeriodDialog); await type('period-account', account); await type('period-org', '組織A')
    await click('period-submit'); expect(wrapper.emitted('submit')).toBeUndefined()
  })
  it('組織名が必須で、先頭ゼロとメモを保つ', async () => {
    await open(StartDevicePeriodDialog); await type('period-account', '00000001')
    await click('period-submit'); expect(wrapper.emitted('submit')).toBeUndefined()
    await type('period-org', ' 組織A '); await type('period-note', ' 確認用 '); await click('period-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ account: '00000001', org_name: '組織A', note: '確認用', access: 'everyone' }]])
  })
  it('access は既定が everyone で、assigned を選べ、引き渡しの注意を出す', async () => {
    await open(StartDevicePeriodDialog); await type('period-account', '00000001'); await type('period-org', '組織A')
    expect(el('period-access-note').textContent).toContain('新しい暗証番号が効くまで')
    await click('period-access-assigned'); await click('period-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ account: '00000001', org_name: '組織A', access: 'assigned' }]])
  })
  it.each([['period-org', 101], ['period-note', 501]] as const)('%s の文字数を検査する', async (id, length) => {
    await open(StartDevicePeriodDialog); await type('period-account', '00000001'); await type('period-org', '組織A')
    await type(id, 'あ'.repeat(length)); await click('period-submit'); expect(wrapper.emitted('submit')).toBeUndefined()
  })
  it('閉じて開くと前の入力を捨てる', async () => {
    await open(StartDevicePeriodDialog); await type('period-account', '00000001')
    await wrapper.setProps({ modelValue: false }); await wrapper.setProps({ modelValue: true }); await flushPromises()
    expect((el('period-account').querySelector('input') as HTMLInputElement).value).toBe('')
  })
  it('サーバの断りをダイアログの中に出す', async () => {
    await open(StartDevicePeriodDialog, { error: '稼働中の利用期間があります' })
    expect(el('start-error').textContent).toContain('稼働中の利用期間があります')
  })
})
describe('割り当てと許可の期間', () => {
  it('停止中と自分のadminは選べ、台帳に無い利用者は除く', async () => {
    await open(AssignDialog, { startedAt: START, selfUserId: UID, users: [
      user(),
      user({ user_id: 'stopped', email: 'stopped@example.com', status: 'suspended' }),
      user({ user_id: null, in_ledger: false, email: 'absent@example.com' }),
    ] })
    const labels = choiceLabels()
    expect(labels).toHaveLength(2)
    expect(labels.find((label) => label.includes('user@example.com'))).toContain('(自分)')
    expect(labels.find((label) => label.includes('stopped@example.com'))).toContain('(停止中)')
    expect(labels.join()).not.toContain('absent@example.com')
  })
  it('利用者の選択が必須でJSTと終了nullを送る', async () => {
    await open(AssignDialog, { startedAt: START, users: [user()] })
    await type('assign-from', '2026-11-04T10:00:00'); await click('assign-submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
    await pick(UID); await click('assign-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ target_type: 'user', user_id: UID, valid_from: '2026-11-04T10:00:00+09:00', valid_to: null }]])
  })
  it('開始の初期値は利用期間の開始と今の遅いほう (JST)', async () => {
    await open(GrantDialog, { startedAt: '2999-01-01T00:00:00Z' })
    expect((el('grant-from').querySelector('input') as HTMLInputElement).value).toMatch(/^2999-01-01T09:00/)
  })
  it('許可はcameraを選択し、秒まで送る', async () => {
    await open(GrantDialog, { startedAt: START }); await type('grant-from', '2026-11-04T10:00:30')
    await type('grant-to', '2026-11-05T00:00'); await click('grant-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ service: 'camera', starts_at: '2026-11-04T10:00:30+09:00', ends_at: '2026-11-05T00:00:00+09:00' }]])
  })
  it.each([
    ['2026-11-04T09:59:59', ''], ['2026-11-04T10:00', '2026-11-04T10:00'],
    ['2026-11-05T10:00', '2026-11-04T10:00'],
  ])('不正な期間 %s〜%s は送らない', async (from, to) => {
    await open(GrantDialog, { startedAt: START }); await type('grant-from', from); await type('grant-to', to)
    await click('grant-submit'); expect(wrapper.emitted('submit')).toBeUndefined()
    expect(el('interval-error').textContent).toBeTruthy()
  })
  it.each([
    [AssignDialog, { users: [user()] }], [GrantDialog, {}],
  ] as const)('サーバの断りをダイアログの中に出す %#', async (component, props) => {
    await open(component, { startedAt: START, error: '期間が重なっています', ...props })
    expect(el('dialog-error').textContent).toContain('期間が重なっています')
  })
  it('対象の通信機アカウントと組織名を出す', async () => {
    await open(GrantDialog, { startedAt: START, account: '00000001', orgName: '組織A' })
    expect(document.body.textContent).toContain('00000001'); expect(document.body.textContent).toContain('組織A')
  })
})
describe('終了の確認', () => {
  it('対象と引き渡しの順序、画面外の作業を表示する', async () => {
    await open(EndConfirmDialog, { account: '00000001', orgName: '組織A' })
    const text = document.body.textContent!
    expect(text).toContain('00000001'); expect(text).toContain('組織A')
    expect(text.indexOf('カメラの利用期間')).toBeLessThan(text.indexOf('暗証番号'))
    expect(text.indexOf('暗証番号')).toBeLessThan(text.indexOf('新しい利用期間'))
    expect(text).toContain('2 と 3'); expect(text).toContain('この画面の外')
    expect(text).toContain('「割り当てだけ」で始め')
    expect(el('handover-steps').querySelectorAll('li')).toHaveLength(5)
    await click('confirm-ok'); expect(wrapper.emitted('confirm')).toHaveLength(1)
  })
})
it.each([
  [StartDevicePeriodDialog, 'period-submit', 'period-cancel', {}],
  [AssignDialog, 'assign-submit', 'assign-cancel', { startedAt: START, users: [user()] }],
  [GrantDialog, 'grant-submit', 'grant-cancel', { startedAt: START }],
  [EndConfirmDialog, 'confirm-ok', 'confirm-cancel', { account: '00000001', orgName: '組織A' }],
])('読み込み中は入力と実行・取り消しを止める %s', async (component, submit, cancel, props) => {
  await open(component, { ...props, loading: true })
  expect(el(submit).hasAttribute('disabled') || el(submit).classList.contains('v-btn--loading')).toBe(true)
  expect(el(cancel).hasAttribute('disabled')).toBe(true)
  await click(submit); expect(wrapper.emitted('submit')).toBeUndefined(); expect(wrapper.emitted('confirm')).toBeUndefined()
  // auto-grow の textarea が高さを測るための隠しの複製 (v-textarea__sizer) は入力欄ではない
  for (const input of document.querySelectorAll('input,textarea')) {
    if (!input.classList.contains('v-textarea__sizer')) expect((input as HTMLInputElement).disabled).toBe(true)
  }
})
