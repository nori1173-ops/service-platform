import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import type { UserSummary } from '../types'
import UsersView from '../views/UsersView.vue'

interface UsersStoreMock {
  users: UserSummary[]
  selfUserId: string
  loading: boolean
  error: string | null
  fetch: Mock
  invite: Mock
}

const holder = vi.hoisted(() => ({ users: null as unknown as UsersStoreMock }))

vi.mock('../stores/users', async () => {
  const { reactive } = await import('vue')
  holder.users = reactive({
    users: [],
    selfUserId: '',
    loading: false,
    error: null,
    fetch: vi.fn(),
    invite: vi.fn(),
  }) as UsersStoreMock
  return { useUsersStore: () => holder.users }
})

const SELF_ID = '01JBEXAMPLE0000000000000001'
const USER_ID = '01JBEXAMPLE0000000000000002'
const TEMPORARY_PASSWORD = 'TempPassw0rdXyz12345'
const StubView = { template: '<div />' }

function makeUser(overrides: Partial<UserSummary> = {}): UserSummary {
  return {
    user_id: USER_ID,
    email: 'target@example.com',
    status: 'active',
    kind: 'customer',
    role: 'user',
    org_name: '株式会社サンプル',
    admin_state: null,
    cognito_status: 'CONFIRMED',
    cognito_enabled: true,
    created_at: '2026-11-04T01:23:00Z',
    updated_at: '2026-11-04T02:45:00Z',
    in_ledger: true,
    ...overrides,
  }
}

let router: Router

async function mountView(): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin-users', name: 'admin-users.users', component: StubView },
      { path: '/admin-users/users/:userId', name: 'admin-users.user', component: StubView },
    ],
  })
  await router.push('/admin-users')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Host = { components: { UsersView }, template: '<KeepAlive><UsersView /></KeepAlive>' }
  const wrapper = mount(Host, { global: { plugins: [vuetify, router] }, attachTo: document.body })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

function el(id: string): HTMLElement {
  return document.querySelector(`[data-test="${id}"]`) as HTMLElement
}

async function fillInviteAndSubmit(): Promise<void> {
  const input = el('invite-email').querySelector('input') as HTMLInputElement
  input.value = 'admin@example.com'
  input.dispatchEvent(new Event('input'))
  el('invite-kind-employee').click()
  await flushPromises()
  ;(el('invite-admin').querySelector('input') as HTMLInputElement).click()
  await flushPromises()
  el('invite-submit').click()
  await flushPromises()
}

beforeEach(() => {
  document.body.innerHTML = ''
  setActivePinia(createPinia())
  Object.assign(holder.users, { users: [], selfUserId: SELF_ID, loading: false, error: null })
  holder.users.fetch.mockReset()
  holder.users.invite.mockReset()
})

describe('UsersView', () => {
  it('開いたときに一覧を取り直す', async () => {
    await mountView()

    expect(holder.users.fetch).toHaveBeenCalled()
  })

  it('メール・種別・組織・状態・役割・登録日時を出す', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    const row = wrapper.find(`[data-test="row-${USER_ID}"]`).text()
    expect(row).toContain('target@example.com')
    expect(row).toContain('お客様')
    expect(row).toContain('株式会社サンプル')
    expect(row).toContain('有効')
    expect(row).toContain('2026-11-04 10:23')
  })

  it.each<[string, Partial<UserSummary>, string]>([
    ['停止中', { status: 'suspended', cognito_enabled: false }, '停止中'],
    ['admin の準備中', { kind: 'employee', role: 'admin', admin_state: 'pending' }, '準備中'],
    ['admin の確認待ち', { kind: 'employee', role: 'admin', admin_state: 'awaiting' }, '確認待ち'],
  ])('%s が読み取れる', async (_label, overrides, text) => {
    holder.users.users = [makeUser(overrides)]
    const wrapper = await mountView()

    expect(wrapper.find(`[data-test="row-${USER_ID}"]`).text()).toContain(text)
  })

  // scripts の途中の失敗などで Cognito にだけいる利用者 (計画 4.2)
  it('台帳に無い利用者は印を付け、詳細へのリンクを付けない', async () => {
    holder.users.users = [makeUser({ user_id: null, in_ledger: false, status: null, kind: null })]
    const wrapper = await mountView()

    const row = wrapper.find('[data-test="row-unregistered-target@example.com"]')
    expect(row.text()).toContain('台帳なし')
    expect(row.find('a').exists()).toBe(false)
  })

  it('自分の行に「自分」を出す', async () => {
    holder.users.users = [makeUser({ user_id: SELF_ID, email: 'me@example.com' }), makeUser()]
    const wrapper = await mountView()

    expect(wrapper.find(`[data-test="row-${SELF_ID}"] [data-test="self-chip"]`).exists()).toBe(true)
    expect(wrapper.find(`[data-test="row-${USER_ID}"] [data-test="self-chip"]`).exists()).toBe(false)
  })

  it('行のメールアドレスから詳細へ行ける', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    const link = wrapper.find(`[data-test="row-${USER_ID}"] a`)
    expect(link.attributes('href')).toBe(`/admin-users/users/${USER_ID}`)
  })

  it('招待するとストアの invite を呼び、成功を知らせる', async () => {
    holder.users.invite.mockResolvedValue({
      ok: true,
      data: { user_id: USER_ID, email: 'admin@example.com', role: 'user' },
    })
    const wrapper = await mountView()

    await wrapper.find('[data-test="invite-open"]').trigger('click')
    await flushPromises()
    await fillInviteAndSubmit()

    expect(holder.users.invite).toHaveBeenCalledWith({
      email: 'admin@example.com',
      kind: 'employee',
      role: 'admin',
    })
    expect(useUiStore().snackbar).toMatchObject({ visible: true, color: 'success' })
  })

  // 一時パスワードは画面で 1 回だけ出し、閉じたら消す (計画 8 章のリスク 10)
  it('admin の招待の一時パスワードを 1 回だけ出し、閉じたら画面から消す', async () => {
    holder.users.invite.mockResolvedValue({
      ok: true,
      data: {
        user_id: USER_ID,
        email: 'admin@example.com',
        role: 'admin',
        temporary_password: TEMPORARY_PASSWORD,
      },
    })
    const wrapper = await mountView()
    await wrapper.find('[data-test="invite-open"]').trigger('click')
    await flushPromises()

    await fillInviteAndSubmit()
    expect(el('one-time-secret').textContent).toContain(TEMPORARY_PASSWORD)

    el('one-time-close').click()
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 400))
    await flushPromises()

    expect(document.body.innerHTML).not.toContain(TEMPORARY_PASSWORD)
    expect(wrapper.html()).not.toContain(TEMPORARY_PASSWORD)
  })

  it('招待の失敗は文言を出し、ダイアログを閉じない', async () => {
    holder.users.invite.mockResolvedValue({
      ok: false,
      code: 'USER_EXISTS',
      message: '同じメールアドレスの利用者がいます',
    })
    const wrapper = await mountView()
    await wrapper.find('[data-test="invite-open"]').trigger('click')
    await flushPromises()

    await fillInviteAndSubmit()

    expect(el('invite-error').textContent).toContain('同じメールアドレスの利用者がいます')
    expect(el('invite-submit')).not.toBeNull()
  })

  it('取得に失敗したらエラーを表示する', async () => {
    holder.users.error = '利用者の一覧を読めませんでした'
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="error"]').text()).toContain('利用者の一覧を読めませんでした')
  })

  it('0 件のときは案内を出す', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="empty"]').exists()).toBe(true)
  })

  it('更新ボタンで取り直す', async () => {
    const wrapper = await mountView()
    holder.users.fetch.mockClear()

    await wrapper.find('[data-test="refresh"]').trigger('click')

    expect(holder.users.fetch).toHaveBeenCalled()
  })
})
