import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { createPinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Me } from '@/stores/me'
import AccountView from './AccountView.vue'

interface MeMock {
  me: Me | null
  loading: boolean
  error: string | null
  errorCode: string | null
  denied: string | null
  load: Mock
}

interface AuthMock {
  email: string | null
  logout: Mock
}

const holder = vi.hoisted(() => ({
  me: null as unknown as MeMock,
  auth: null as unknown as AuthMock,
}))

vi.mock('@/stores/me', async () => {
  const { reactive } = await import('vue')
  holder.me = reactive({
    me: null,
    loading: false,
    error: null,
    errorCode: null,
    denied: null,
    load: vi.fn(),
  }) as MeMock
  return { useMeStore: () => holder.me }
})

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({ email: 'user@example.com', logout: vi.fn() }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

vi.mock('@/components/PasskeyManager.vue', () => ({
  default: { name: 'PasskeyManager', template: '<div data-test="passkey-manager" />' },
}))

vi.mock('@/components/AdminTotpSetup.vue', () => ({
  default: { name: 'AdminTotpSetup', template: '<div data-test="admin-totp-setup" />' },
}))

vi.mock('@/components/AssignmentList.vue', () => ({
  default: { name: 'AssignmentList', template: '<div data-test="assignment-list" />' },
}))

const StubView = { template: '<div />' }

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user_id: 'u-1',
    email: 'user@example.com',
    status: 'active',
    kind: 'customer',
    org_name: '株式会社サンプル',
    is_admin: false,
    admin_state: null,
    mfa_registered: false,
    restore_requires: null,
    ...overrides,
  }
}

let router: Router

async function mountView(): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account', name: 'account', component: StubView },
      { path: '/login', name: 'login', component: StubView },
      { path: '/login/admin', name: 'login-admin', component: StubView },
    ],
  })
  await router.push('/account')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Host = { components: { AccountView }, template: '<KeepAlive><AccountView /></KeepAlive>' }
  const wrapper = mount(Host, { global: { plugins: [createPinia(), vuetify, router] } })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

beforeEach(() => {
  Object.assign(holder.me, { me: null, loading: false, error: null, errorCode: null, denied: null })
  holder.me.load.mockReset()
  holder.auth.logout.mockReset()
})

describe('AccountView', () => {
  it('開くたびに自分の状態を読み直す', async () => {
    holder.me.me = makeMe()

    await mountView()

    expect(holder.me.load).toHaveBeenCalled()
  })

  it('メールアドレス・種別・組織を出す', async () => {
    holder.me.me = makeMe()

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('user@example.com')
    expect(wrapper.text()).toContain('お客様')
    expect(wrapper.text()).toContain('株式会社サンプル')
  })

  // 再招待の後の停止中でも、本人がパスキーを登録できる (ADR D8 の「再招待の後の復帰」)
  it.each([
    ['有効', makeMe()],
    ['停止中', makeMe({ status: 'suspended' })],
  ])('admin でない利用者 (%s) にはパスキーの管理を出す', async (_label, me) => {
    holder.me.me = me

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="passkey-manager"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="admin-totp-setup"]').exists()).toBe(false)
  })

  // 有効化が終わるまでは TOTP の登録だけを案内する (ADR D9)
  it('準備中の admin には TOTP の登録を出し、パスキーの管理は出さない', async () => {
    holder.me.me = makeMe({ kind: 'employee', is_admin: true, admin_state: 'pending' })

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="admin-totp-setup"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="passkey-manager"]').exists()).toBe(false)
  })

  it.each<[string, Partial<Me>]>([
    ['確認待ち', { admin_state: 'awaiting', mfa_registered: true }],
    ['有効', { admin_state: 'active', mfa_registered: true }],
  ])('%s の admin には TOTP の登録を出さない', async (_label, overrides) => {
    holder.me.me = makeMe({ kind: 'employee', is_admin: true, ...overrides })

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="admin-totp-setup"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="passkey-manager"]').exists()).toBe(overrides.admin_state === 'active')
  })

  it('状態が読めないときはパスキーの管理も TOTP の登録も出さない', async () => {
    holder.me.error = 'この利用者は登録されていません'
    holder.me.errorCode = 'USER_NOT_REGISTERED'

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('この利用者は登録されていません')
    expect(wrapper.find('[data-test="passkey-manager"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="admin-totp-setup"]').exists()).toBe(false)
  })

  // 共用の端末に備え、サインアウトを常に出す (ADR D1)
  it('サインアウトでサインインの画面へ戻る', async () => {
    holder.me.me = makeMe()
    const wrapper = await mountView()

    await wrapper.find('[data-test="account-signout"]').trigger('click')
    await flushPromises()

    expect(holder.auth.logout).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('admin のサインアウトは管理者のサインインの画面へ戻る', async () => {
    holder.me.me = makeMe({ kind: 'employee', is_admin: true, admin_state: 'active' })
    const wrapper = await mountView()

    await wrapper.find('[data-test="account-signout"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login-admin')
  })

  // ADR D5 のアカウント単位: 割り当てと許可の一覧。API は有効な利用者だけが呼べる
  it.each<[string, Partial<Me>, boolean]>([
    ['有効なお客様', {}, true],
    ['有効な admin (自分を割り当てて使う。判断 9)', { kind: 'employee', is_admin: true, admin_state: 'active' }, true],
    ['停止中', { status: 'suspended' }, false],
    ['確認待ちの admin', { kind: 'employee', is_admin: true, admin_state: 'awaiting' }, false],
  ])('%s には割り当ての一覧を出すか: %s', async (_label, overrides, shown) => {
    holder.me.me = makeMe(overrides)

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="assignment-list"]').exists()).toBe(shown)
  })

  it('状態が読めないときもサインアウトできる', async () => {
    holder.me.error = '読めませんでした'
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="account-signout"]').exists()).toBe(true)
  })
})


it('有効な admin にはパスキー登録後の両方の入口を案内する', async () => {
  holder.me.me = makeMe({ kind: 'employee', is_admin: true, admin_state: 'active' })
  const wrapper = await mountView()
  expect(wrapper.find('[data-test="passkey-manager"]').exists()).toBe(true)
  expect(wrapper.text()).toContain('通常のサインインからパスキーでも入れます')
  expect(wrapper.text()).toContain('パスワードと認証アプリの入口も残ります')
})
