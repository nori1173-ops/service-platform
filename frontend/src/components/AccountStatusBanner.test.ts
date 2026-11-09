import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Me } from '@/stores/me'
import { usePasskeysStore } from '@/stores/passkeys'
import AccountStatusBanner from './AccountStatusBanner.vue'

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user_id: 'u-1',
    email: 'user@example.com',
    status: 'active',
    restore_requires: null,
    kind: 'customer',
    org_name: null,
    is_admin: false,
    admin_state: null,
    mfa_registered: false,
    ...overrides,
  }
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

function mountBanner(props: {
  me: Me | null
  error?: string | null
  denied?: string | null
}): VueWrapper {
  const vuetify = createVuetify({ components, directives })
  return mount(AccountStatusBanner, {
    props: { error: null, denied: null, ...props },
    global: { plugins: [pinia, vuetify] },
  })
}

describe('AccountStatusBanner', () => {
  it.each([null, 'reinvite'] as const)('有効な利用者は restore_requires=%s でも何も出さない', (restoreRequires) => {
    const wrapper = mountBanner({ me: makeMe({ restore_requires: restoreRequires }) })

    expect(wrapper.find('.v-alert').exists()).toBe(false)
  })

  it.each([false, true])('通常の停止中はパスキーの登録済み=%s でも今の案内を出す', (registered) => {
    if (registered) {
      usePasskeysStore().items = [{ credentialId: 'cred-1', name: null, createdAt: null, attachment: null }]
    }
    const wrapper = mountBanner({ me: makeMe({ status: 'suspended' }) })

    expect(wrapper.find('[data-test="status-suspended"]').text()).toBe(
      'このアカウントは停止中です。サービスは使えません。再招待を受けた方は、この画面でパスキーを登録してから管理者へお知らせください。',
    )
  })

  it('再招待の後は下のボタンからパスキーを登録し管理者の有効化を待つよう案内する', () => {
    usePasskeysStore().loaded = true
    const wrapper = mountBanner({ me: makeMe({ status: 'suspended', restore_requires: 'reinvite' }) })

    expect(wrapper.find('[data-test="status-suspended"]').text()).toBe(
      '再招待の後の確認待ちです。下の「この端末でパスキーを登録する」でパスキーを登録してください。登録を確かめた後、管理者が有効にします。',
    )
  })

  it('再招待の後にパスキーが1つあれば管理者の有効化を待つよう案内する', () => {
    usePasskeysStore().loaded = true
    usePasskeysStore().items = [{ credentialId: 'cred-1', name: null, createdAt: null, attachment: null }]
    const wrapper = mountBanner({ me: makeMe({ status: 'suspended', restore_requires: 'reinvite' }) })

    expect(wrapper.find('[data-test="status-suspended"]').text()).toBe(
      'パスキーは登録済みです。管理者が有効にするまでお待ちください。',
    )
  })

  it('再招待の後のパスキー一覧の変化に合わせて案内が切り替わる', async () => {
    const passkeys = usePasskeysStore()
    const wrapper = mountBanner({ me: makeMe({ status: 'suspended', restore_requires: 'reinvite' }) })
    const banner = wrapper.find('[data-test="status-suspended"]')

    expect(banner.text()).toBe('再招待の後の確認待ちです。')

    passkeys.loaded = true
    await nextTick()

    expect(banner.text()).toContain('パスキーを登録してください')

    passkeys.items = [{ credentialId: 'cred-1', name: null, createdAt: null, attachment: null }]
    await nextTick()

    expect(banner.text()).toBe('パスキーは登録済みです。管理者が有効にするまでお待ちください。')

    passkeys.items = []
    await nextTick()

    expect(banner.text()).toContain('パスキーを登録してください')
  })

  it.each([null, '一覧を取得できませんでした'])('再招待の後で一覧の取得前はエラー=%s でも登録を案内しない', (error) => {
    usePasskeysStore().error = error
    const wrapper = mountBanner({ me: makeMe({ status: 'suspended', restore_requires: 'reinvite' }) })

    expect(wrapper.find('[data-test="status-suspended"]').text()).toBe('再招待の後の確認待ちです。')
  })

  it.each([
    ['pending', false],
    ['pending', true],
    ['awaiting', false],
    ['awaiting', true],
  ] as const)('再招待の後の admin は状態=%s・一覧取得済み=%s でもパスキーを案内しない', (adminState, loaded) => {
    const passkeys = usePasskeysStore()
    passkeys.loaded = loaded
    if (loaded) {
      passkeys.items = [{ credentialId: 'cred-1', name: null, createdAt: null, attachment: null }]
    }
    const wrapper = mountBanner({
      me: makeMe({ is_admin: true, status: 'suspended', restore_requires: 'reinvite', admin_state: adminState }),
    })

    const text = wrapper.find('[data-test="status-suspended"]').text()
    expect(text).toBe('再招待の後のため、停止中です。認証アプリ (TOTP) の登録と、別の管理者 (または運用者) の確認を終えると有効になります。')
    expect(text).not.toContain('パスキー')
    expect(wrapper.find(`[data-test="status-${adminState}"]`).exists()).toBe(true)
  })

  it('admin の準備中は TOTP の登録を促す', () => {
    const wrapper = mountBanner({ me: makeMe({ is_admin: true, admin_state: 'pending' }) })

    const text = wrapper.find('[data-test="status-pending"]').text()
    expect(text).toContain('準備中')
    expect(text).toContain('認証アプリ')
  })

  it('admin の確認待ちは、別の管理者の確認とサインインし直しを案内する', () => {
    const wrapper = mountBanner({ me: makeMe({ is_admin: true, admin_state: 'awaiting' }) })

    const text = wrapper.find('[data-test="status-awaiting"]').text()
    expect(text).toContain('確認待ち')
    expect(text).toContain('サインインし直')
  })

  it('TOTP を通る前のセッションで断られたらサインインし直しを案内する', () => {
    const wrapper = mountBanner({
      me: makeMe({ is_admin: true, admin_state: 'active' }),
      denied: 'ADMIN_SESSION_STALE',
    })

    expect(wrapper.find('[data-test="status-denied"]').text()).toContain('サインインし直')
  })

  it('状態が読めないときはその文言を出す', () => {
    const wrapper = mountBanner({ me: null, error: 'この利用者は登録されていません' })

    expect(wrapper.find('[data-test="status-error"]').text()).toContain(
      'この利用者は登録されていません',
    )
  })

  it('読み直しのボタンで retry を出す', async () => {
    const wrapper = mountBanner({ me: null, error: '読めませんでした' })

    await wrapper.find('[data-test="status-retry"]').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})
