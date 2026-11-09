import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { PasskeyItem } from '@/stores/passkeys'
import { baseApi } from '@/services/baseApi'
import { useUiStore } from '@/stores/ui'
import PasskeyManager from './PasskeyManager.vue'

interface PasskeysMock {
  items: PasskeyItem[]
  loaded: boolean
  loading: boolean
  error: string | null
  atLimit: boolean
  fetch: Mock
  add: Mock
  remove: Mock
}

vi.mock('@/services/baseApi', () => ({ baseApi: { enablePasskeyMfa: vi.fn() } }))

const holder = vi.hoisted(() => ({ passkeys: null as unknown as PasskeysMock }))

vi.mock('@/stores/passkeys', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/stores/passkeys')>()
  const { reactive } = await import('vue')
  holder.passkeys = reactive({
    items: [],
    loaded: true,
    loading: false,
    error: null,
    atLimit: false,
    fetch: vi.fn(),
    add: vi.fn(),
    remove: vi.fn(),
  }) as PasskeysMock
  return { ...actual, usePasskeysStore: () => holder.passkeys }
})

function item(id: string, overrides: Partial<PasskeyItem> = {}): PasskeyItem {
  return {
    credentialId: id,
    name: 'Device-bound Passkey',
    createdAt: new Date(2026, 10, 4, 10, 23),
    attachment: 'platform',
    ...overrides,
  }
}

async function mountManager(admin = false): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(PasskeyManager, { props: { admin }, global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  vi.restoreAllMocks()
})

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(baseApi.enablePasskeyMfa).mockReset().mockResolvedValue({ passkey_mfa_enabled: true })
  document.body.innerHTML = ''
  setActivePinia(createPinia())
  Object.assign(holder.passkeys, { items: [], loaded: true, loading: false, error: null, atLimit: false })
  holder.passkeys.fetch.mockReset()
  holder.passkeys.add.mockReset().mockResolvedValue(true)
  holder.passkeys.remove.mockReset().mockResolvedValue(true)
})

describe('PasskeyManager', () => {
  it('開いたときに一覧を読む', async () => {
    await mountManager()

    expect(holder.passkeys.fetch).toHaveBeenCalled()
  })

  it('名前・登録日時・認証器の種類を出す', async () => {
    holder.passkeys.items = [
      item('cred-1'),
      item('cred-2', { name: 'Roaming passkey', attachment: 'cross-platform' }),
      item('cred-3', { name: null, attachment: null, createdAt: null }),
    ]
    const wrapper = await mountManager()

    const first = wrapper.find('[data-test="passkey-row-cred-1"]').text()
    expect(first).toContain('Device-bound Passkey')
    expect(first).toContain('2026-11-04 10:23')
    expect(first).toContain('この端末に組み込み')
    expect(wrapper.find('[data-test="passkey-row-cred-2"]').text()).toContain('別の機器')
    expect(wrapper.find('[data-test="passkey-row-cred-3"]').text()).toContain('—')
  })

  // 初回のサインインの直後に登録を促す (ADR D1)
  it('0 個なら登録を促す', async () => {
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="passkey-empty"]').exists()).toBe(true)
  })

  it('端末ごとの登録を勧め、20 個の上限を案内する', async () => {
    holder.passkeys.items = [item('cred-1')]
    const wrapper = await mountManager()

    const advice = wrapper.find('[data-test="passkey-advice"]').text()
    expect(advice).toContain('端末ごと')
    expect(advice).toContain('20')
  })

  it('追加でストアの add を呼び、成功を知らせる', async () => {
    const wrapper = await mountManager()

    await wrapper.find('[data-test="passkey-add"]').trigger('click')
    await flushPromises()

    expect(holder.passkeys.add).toHaveBeenCalledTimes(1)
    expect(useUiStore().snackbar).toMatchObject({ visible: true, color: 'success' })
  })

  it('追加に失敗したらストアの文言を出す', async () => {
    holder.passkeys.add.mockImplementation(async () => {
      holder.passkeys.error = 'パスキーの登録が取り消されました'
      return false
    })
    const wrapper = await mountManager()

    await wrapper.find('[data-test="passkey-add"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="passkey-error"]').text()).toContain('取り消されました')
  })

  it('上限に達したら追加できない', async () => {
    holder.passkeys.items = [item('cred-1')]
    holder.passkeys.atLimit = true
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="passkey-add"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="passkey-limit"]').exists()).toBe(true)
  })

  it('削除は確かめてから行う', async () => {
    holder.passkeys.items = [item('cred-1')]
    const wrapper = await mountManager()

    await wrapper.find('[data-test="passkey-delete-cred-1"]').trigger('click')
    await flushPromises()
    expect(holder.passkeys.remove).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('パスキーを削除しますか')

    ;(document.querySelector('[data-test="confirm-ok"]') as HTMLElement).click()
    await flushPromises()

    expect(holder.passkeys.remove).toHaveBeenCalledWith('cred-1')
  })

  it('削除をやめると何もしない', async () => {
    holder.passkeys.items = [item('cred-1')]
    const wrapper = await mountManager()

    await wrapper.find('[data-test="passkey-delete-cred-1"]').trigger('click')
    await flushPromises()
    ;(document.querySelector('[data-test="confirm-cancel"]') as HTMLElement).click()
    await flushPromises()

    expect(holder.passkeys.remove).not.toHaveBeenCalled()
  })
})


it('admin の登録・別端末・削除ではメールのコードを勧めない', async () => {
  const wrapper = await mountManager(true)
  expect(wrapper.text()).not.toContain('メールのコード')
  expect(wrapper.text()).toContain('パスワードと認証アプリ')
  holder.passkeys.items = [item('cred-1')]
  await flushPromises()
  await wrapper.get('[data-test="passkey-delete-cred-1"]').trigger('click')
  await flushPromises()
  expect(document.body.textContent).not.toContain('メールのコード')
})


it('admin はパスキーの登録後に利用設定を済ませてから成功を知らせる', async () => {
  const wrapper = await mountManager(true)
  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledOnce()
  expect(holder.passkeys.add.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(baseApi.enablePasskeyMfa).mock.invocationCallOrder[0]!)
  expect(useUiStore().snackbar.color).toBe('success')
})

it('admin の利用設定が失敗したら登録をやり直さず再試行できる', async () => {
  vi.mocked(baseApi.enablePasskeyMfa).mockRejectedValueOnce(new Error('unavailable'))
  const wrapper = await mountManager(true)
  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('[data-test="passkey-mfa-error"]').text()).toContain('パスキーの利用設定')
  expect(useUiStore().snackbar.visible).toBe(false)
  await wrapper.get('[data-test="passkey-enable"]').trigger('click')
  await flushPromises()
  expect(holder.passkeys.add).toHaveBeenCalledOnce()
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledTimes(2)
  expect(wrapper.find('[data-test="passkey-mfa-error"]').exists()).toBe(false)
})

it('admin は印がなければ登録済みのパスキーの利用設定を開くたびに呼ばない', async () => {
  holder.passkeys.items = [item('cred-1')]
  await mountManager(true)
  expect(baseApi.enablePasskeyMfa).not.toHaveBeenCalled()
})

it('admin でない利用者は利用設定 API を呼ばない', async () => {
  const wrapper = await mountManager()
  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(baseApi.enablePasskeyMfa).not.toHaveBeenCalled()
})


it('admin の追加後の設定失敗を記録し、次回に1回だけ再試行して成功で消す', async () => {
  vi.mocked(baseApi.enablePasskeyMfa).mockRejectedValueOnce(new Error('unavailable'))
  const wrapper = await mountManager(true)
  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(sessionStorage.getItem('service-platform.admin-passkey-mfa-pending')).toBe('1')
  wrapper.unmount()
  const next = await mountManager(true)
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledTimes(2)
  expect(sessionStorage.getItem('service-platform.admin-passkey-mfa-pending')).toBeNull()
  expect(next.find('[data-test="passkey-mfa-error"]').exists()).toBe(false)
  next.unmount()
  await mountManager(true)
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledTimes(2)
})

it('admin の次回の再試行も失敗したら印と再試行ボタンを残す', async () => {
  sessionStorage.setItem('service-platform.admin-passkey-mfa-pending', '1')
  vi.mocked(baseApi.enablePasskeyMfa).mockRejectedValueOnce(new Error('unavailable'))
  const wrapper = await mountManager(true)
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledOnce()
  expect(sessionStorage.getItem('service-platform.admin-passkey-mfa-pending')).toBe('1')
  expect(wrapper.find('[data-test="passkey-enable"]').exists()).toBe(true)
  await flushPromises()
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledOnce()
})

it('パスキーが無い409では印を消して登録を案内し、次の追加で利用設定を呼ぶ', async () => {
  sessionStorage.setItem('service-platform.admin-passkey-mfa-pending', '1')
  vi.mocked(baseApi.enablePasskeyMfa).mockRejectedValueOnce({
    response: { status: 409, data: { error: { code: 'PASSKEY_REQUIRED', message: '先にパスキーを登録してください' } } },
  })
  const wrapper = await mountManager(true)
  expect(wrapper.get('[data-test="passkey-mfa-error"]').text()).toContain('先にパスキーを登録してください')
  expect(wrapper.find('[data-test="passkey-enable"]').exists()).toBe(false)
  expect(useUiStore().snackbar.visible).toBe(false)
  expect(sessionStorage.getItem('service-platform.admin-passkey-mfa-pending')).toBeNull()
  wrapper.unmount()
  const next = await mountManager(true)
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledOnce()
  await next.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledTimes(2)
  expect(useUiStore().snackbar).toMatchObject({ visible: true, color: 'success' })
})

it('sessionStorage が例外を投げても追加と通知は成功する', async () => {
  const unavailable = () => { throw new Error('storage unavailable') }
  const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(unavailable)
  const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(unavailable)
  const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(unavailable)
  vi.mocked(baseApi.enablePasskeyMfa).mockRejectedValueOnce(new Error('unavailable'))
  const wrapper = await mountManager(true)
  expect(getItem).toHaveBeenCalledWith('service-platform.admin-passkey-mfa-pending')
  expect(baseApi.enablePasskeyMfa).not.toHaveBeenCalled()

  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(setItem).toHaveBeenCalledWith('service-platform.admin-passkey-mfa-pending', '1')
  expect(wrapper.find('[data-test="passkey-enable"]').exists()).toBe(true)
  await wrapper.get('[data-test="passkey-enable"]').trigger('click')
  await flushPromises()
  expect(removeItem).toHaveBeenCalledWith('service-platform.admin-passkey-mfa-pending')
  expect(wrapper.find('[data-test="passkey-mfa-error"]').exists()).toBe(false)

  await wrapper.get('[data-test="passkey-add"]').trigger('click')
  await flushPromises()
  expect(holder.passkeys.add).toHaveBeenCalledTimes(2)
  expect(baseApi.enablePasskeyMfa).toHaveBeenCalledTimes(3)
  expect(useUiStore().snackbar).toMatchObject({
    visible: true, color: 'success', text: 'パスキーを登録しました。次からこのパスキーでサインインできます',
  })
})
