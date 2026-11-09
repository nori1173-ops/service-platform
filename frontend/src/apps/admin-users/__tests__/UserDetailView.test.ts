import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useMeStore, type Me } from '@/stores/me'
import { useUiStore } from '@/stores/ui'
import type { UserDetail } from '../types'
import UserDetailView from '../views/UserDetailView.vue'

interface UsersStoreMock {
  selfUserId: string
  detail: UserDetail | null
  detailLoading: boolean
  detailError: string | null
  fetch: Mock
  fetchDetail: Mock
  suspend: Mock
  restore: Mock
  reinvite: Mock
  activateAdmin: Mock
  issueTemporaryPassword: Mock
  revokeAdmin: Mock
  changeEmail: Mock
  updateProfile: Mock
}

const OPERATIONS = [
  'suspend',
  'restore',
  'reinvite',
  'activateAdmin',
  'issueTemporaryPassword',
  'revokeAdmin',
  'changeEmail',
  'updateProfile',
] as const

const holder = vi.hoisted(() => ({ users: null as unknown as UsersStoreMock }))

vi.mock('../stores/users', async () => {
  const { reactive } = await import('vue')
  holder.users = reactive({
    selfUserId: '',
    detail: null,
    detailLoading: false,
    detailError: null,
    fetch: vi.fn(),
    fetchDetail: vi.fn(),
    suspend: vi.fn(),
    restore: vi.fn(),
    reinvite: vi.fn(),
    activateAdmin: vi.fn(),
    issueTemporaryPassword: vi.fn(),
    revokeAdmin: vi.fn(),
    changeEmail: vi.fn(),
    updateProfile: vi.fn(),
  }) as UsersStoreMock
  return { useUsersStore: () => holder.users }
})

const SELF_ID = '01JBEXAMPLE0000000000000001'
const USER_ID = '01JBEXAMPLE0000000000000002'
const OTHER_ID = '01JBEXAMPLE0000000000000003'
const TEMPORARY_PASSWORD = 'TempPassw0rdXyz12345'
const StubView = { template: '<div />' }

function makeDetail(overrides: Partial<UserDetail> = {}): UserDetail {
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
    created_at: '2026-11-04T01:00:00Z',
    updated_at: '2026-11-04T02:00:00Z',
    in_ledger: true,
    contact: '代表 03-0000-0000',
    note: '引き渡し済み',
    is_admin: false,
    totp: false,
    factors: { password: false, web_authn: true, email_otp: true },
    mfa_enabled_at: null,
    admin_verified_at: null,
    admin_verified_method: null,
    restore_requires: null,
    reinvite_in_progress: false,
    email_pending: null,
    reinvite_count: 0,
    suspended_at: null,
    suspend_reason: null,
    assignments: [],
    ...overrides,
  }
}

function adminDetail(overrides: Partial<UserDetail> = {}): UserDetail {
  return makeDetail({
    email: 'admin@example.com',
    kind: 'employee',
    role: 'admin',
    org_name: null,
    is_admin: true,
    totp: true,
    admin_state: 'active',
    factors: { password: true, web_authn: false, email_otp: true },
    mfa_enabled_at: '2026-11-04T03:00:00Z',
    ...overrides,
  })
}

let router: Router
// 前のテストの画面が共有のストアの変化で描き直されないよう、テストごとに外す
let mounted: VueWrapper[] = []

function signedInAs(userId: string): void {
  const me: Me = {
    user_id: userId,
    email: 'me@example.com',
    status: 'active',
    kind: 'employee',
    org_name: null,
    is_admin: true,
    admin_state: 'active',
    mfa_registered: true,
    restore_requires: null,
  }
  useMeStore().me = me
}

async function mountView(
  detail: UserDetail | null = makeDetail(),
  routeId = detail?.user_id ?? USER_ID,
): Promise<VueWrapper> {
  holder.users.detail = detail
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin-users', name: 'admin-users.users', component: StubView },
      { path: '/admin-users/device-periods/:id', name: 'admin-users.device-period', component: StubView },
      {
        path: '/admin-users/users/:userId',
        name: 'admin-users.user',
        component: UserDetailView,
        props: true,
      },
    ],
  })
  await router.push(`/admin-users/users/${routeId}`)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Host = { template: '<RouterView v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></RouterView>' }
  const wrapper = mount(Host, { global: { plugins: [vuetify, router] }, attachTo: document.body })
  mounted.push(wrapper as unknown as VueWrapper)
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

function el(id: string): HTMLElement {
  const found = document.querySelector(`[data-test="${id}"]`) as HTMLElement | null
  if (!found) {
    throw new Error(`[data-test="${id}"] が見つかりません`)
  }
  return found
}

function exists(id: string): boolean {
  return document.querySelector(`[data-test="${id}"]`) !== null
}

async function click(id: string): Promise<void> {
  el(id).click()
  await flushPromises()
}

async function type(id: string, value: string): Promise<void> {
  const input = el(id).querySelector('input, textarea') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

afterEach(() => {
  for (const wrapper of mounted) {
    wrapper.unmount()
  }
  mounted = []
})

beforeEach(() => {
  document.body.innerHTML = ''
  setActivePinia(createPinia())
  Object.assign(holder.users, {
    selfUserId: SELF_ID,
    detail: null,
    detailLoading: false,
    detailError: null,
  })
  holder.users.fetch.mockReset()
  holder.users.fetchDetail.mockReset()
  for (const name of OPERATIONS) {
    holder.users[name].mockReset().mockResolvedValue({ ok: true, data: { user_id: USER_ID } })
  }
})

describe('UserDetailView: 表示', () => {
  it('開いたときに URL の利用者の詳細を読む', async () => {
    await mountView()

    expect(holder.users.fetchDetail).toHaveBeenCalledWith(USER_ID)
  })

  it('台帳の項目・連絡先・サインインの要素を出す', async () => {
    await mountView()

    const text = document.body.textContent ?? ''
    expect(text).toContain('target@example.com')
    expect(text).toContain('株式会社サンプル')
    expect(text).toContain('代表 03-0000-0000')
    expect(text).toContain('引き渡し済み')
    expect(el('factor-web_authn').textContent).toContain('あり')
    expect(el('factor-password').textContent).toContain('なし')
  })

  // お客様がパスワードを後から足していれば admin が見つけられる (ADR D1 の「パスワードの後付け」)
  it('お客様にパスワードがあれば注意を出す', async () => {
    await mountView(makeDetail({ factors: { password: true, web_authn: true, email_otp: true } }))

    expect(el('password-warning').textContent).toContain('パスワード')
  })

  it('admin の状態・TOTP・登録の時刻を出す', async () => {
    await mountView(adminDetail({ admin_state: 'awaiting' }))

    const text = document.body.textContent ?? ''
    expect(text).toContain('確認待ち')
    expect(el('totp').textContent).toContain('あり')
    expect(el('mfa-enabled-at').textContent).toContain('2026-11-04 12:00')
  })

  it('読めなかったら文言を出す', async () => {
    holder.users.detailError = '利用者が見つかりません。一覧を更新してください'
    await mountView(null)

    expect(el('detail-error').textContent).toContain('利用者が見つかりません')
  })

  it('再招待の途中なら注意を出す', async () => {
    await mountView(makeDetail({ status: 'suspended', reinvite_in_progress: true }))

    expect(el('reinvite-in-progress').textContent).toContain('再招待が終わっていません')
  })
})

describe('UserDetailView: 操作の出し分け', () => {
  // 自分自身への停止・再招待・admin を外す・有効化・一時パスワードは API も 403 で断る
  it('自分自身には操作できない', async () => {
    signedInAs(SELF_ID)
    await mountView(adminDetail({ user_id: SELF_ID }))

    for (const id of ['op-suspend', 'op-reinvite', 'op-temporary-password', 'op-revoke-admin']) {
      expect((el(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
  })

  // 一覧を読まずに直接 URL で開いても、自分かどうかは me の user_id で決める
  it('直接 URL で自分の詳細を開いても自己操作のボタンを押せない', async () => {
    holder.users.selfUserId = ''
    signedInAs(SELF_ID)
    await mountView(adminDetail({ user_id: SELF_ID }))

    for (const id of ['op-suspend', 'op-reinvite', 'op-temporary-password', 'op-revoke-admin']) {
      expect((el(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
  })

  it('自分でなければ操作のボタンを押せる', async () => {
    holder.users.selfUserId = ''
    signedInAs(SELF_ID)
    await mountView(adminDetail())

    expect((el('op-suspend') as HTMLButtonElement).disabled).toBe(false)
  })

  it('有効な利用者には停止を出し、有効に戻すは出さない', async () => {
    await mountView()

    expect(exists('op-suspend')).toBe(true)
    expect(exists('op-restore')).toBe(false)
  })

  it('停止中の利用者には有効に戻すを出す', async () => {
    await mountView(makeDetail({ status: 'suspended', cognito_enabled: false }))

    expect(exists('op-restore')).toBe(true)
  })

  it('停止が Cognito まで済んでいなければ停止の流し直しを出す', async () => {
    await mountView(makeDetail({ status: 'suspended', cognito_enabled: true }))

    expect(exists('op-suspend')).toBe(true)
  })

  // 再招待の後の admin は「admin を有効にする」で戻す (ADR D8 / D9)
  it('再招待の後の admin には有効に戻すを出さず、確認待ちなら admin を有効にするを出す', async () => {
    await mountView(
      adminDetail({ status: 'suspended', restore_requires: 'reinvite', admin_state: 'awaiting' }),
    )

    expect(exists('op-restore')).toBe(false)
    expect(exists('op-activate-admin')).toBe(true)
  })

  it('admin でなければ一時パスワードと admin を外すを出さない', async () => {
    await mountView()

    expect(exists('op-temporary-password')).toBe(false)
    expect(exists('op-revoke-admin')).toBe(false)
    expect(exists('op-activate-admin')).toBe(false)
  })
})

describe('UserDetailView: 操作', () => {
  it('停止は理由を添えてストアを呼び、成功を知らせる', async () => {
    await mountView()

    await click('op-suspend')
    await type('suspend-reason', '利用の終了')
    await click('suspend-submit')

    expect(holder.users.suspend).toHaveBeenCalledWith(USER_ID, '利用の終了')
    expect(useUiStore().snackbar).toMatchObject({ visible: true, color: 'success' })
  })

  it('有効に戻すは確認の方法を添えてストアを呼ぶ', async () => {
    await mountView(makeDetail({ status: 'suspended', cognito_enabled: false }))

    await click('op-restore')
    await click('verify-method-phone')
    await type('verify-note', '窓口に電話で確認')
    await click('verify-submit')

    expect(holder.users.restore).toHaveBeenCalledWith(USER_ID, {
      verified_method: 'phone',
      note: '窓口に電話で確認',
    })
  })

  it('admin を有効にするは確認の方法を添えてストアを呼ぶ', async () => {
    await mountView(adminDetail({ admin_state: 'awaiting' }))

    await click('op-activate-admin')
    expect(document.body.textContent).toContain('2026-11-04 12:00')
    await click('verify-method-in_person')
    await click('verify-submit')

    expect(holder.users.activateAdmin).toHaveBeenCalledWith(USER_ID, { method: 'in_person' })
  })

  it('再招待は理由を添えてストアを呼ぶ', async () => {
    await mountView()

    await click('op-reinvite')
    await type('reinvite-reason', '端末の盗難')
    await click('reinvite-submit')

    expect(holder.users.reinvite).toHaveBeenCalledWith(USER_ID, { reason: '端末の盗難', role: 'user' })
  })

  it.each([
    [
      '一時パスワードの発行',
      async () => {
        await click('op-temporary-password')
        await click('confirm-ok')
      },
      'issueTemporaryPassword',
    ],
    [
      'admin の再招待',
      async () => {
        await click('op-reinvite')
        await type('reinvite-reason', 'TOTP の紛失')
        await click('reinvite-submit')
      },
      'reinvite',
    ],
  ] as const)('%s の一時パスワードを 1 回だけ出し、閉じたら画面から消す', async (_label, act, op) => {
    holder.users[op].mockResolvedValue({
      ok: true,
      data: { user_id: USER_ID, temporary_password: TEMPORARY_PASSWORD },
    })
    await mountView(adminDetail())

    await act()
    expect(el('one-time-secret').textContent).toContain(TEMPORARY_PASSWORD)

    await click('one-time-close')

    expect(document.body.innerHTML).not.toContain(TEMPORARY_PASSWORD)
  })

  it('admin を外すは確かめてからストアを呼ぶ', async () => {
    await mountView(adminDetail())

    await click('op-revoke-admin')
    expect(holder.users.revokeAdmin).not.toHaveBeenCalled()
    await click('confirm-ok')

    expect(holder.users.revokeAdmin).toHaveBeenCalledWith(USER_ID)
  })

  it('email の変更は新しい宛先でストアを呼ぶ', async () => {
    await mountView()

    await click('op-email')
    await type('email-new', 'next@example.com')
    await click('email-submit')

    expect(holder.users.changeEmail).toHaveBeenCalledWith(USER_ID, 'next@example.com')
  })

  it('項目の変更は 3 つの項目でストアを呼ぶ', async () => {
    await mountView()

    await click('op-profile')
    await type('profile-note', '')
    await click('profile-submit')

    expect(holder.users.updateProfile).toHaveBeenCalledWith(USER_ID, {
      org_name: '株式会社サンプル',
      contact: '代表 03-0000-0000',
      note: '',
    })
  })

  it('失敗は API の文言をスナックバーに出す', async () => {
    holder.users.suspend.mockResolvedValue({
      ok: false,
      code: 'LAST_ADMIN',
      message: '最後の有効な管理者には操作できません',
    })
    await mountView()

    await click('op-suspend')
    await click('suspend-submit')

    expect(useUiStore().snackbar).toMatchObject({
      visible: true,
      color: 'error',
      text: '最後の有効な管理者には操作できません',
    })
  })
})

describe('UserDetailView: 確認した利用者と操作する利用者を取り違えない', () => {
  function otherDetail(): UserDetail {
    return adminDetail({ user_id: OTHER_ID, email: 'other@example.com' })
  }

  // 確認を開いた後に、遅れて届いた別の利用者の詳細で store.detail が替わる場合 (レビューの Critical)
  it.each([
    ['一時パスワードの発行', 'op-temporary-password', 'issueTemporaryPassword'],
    ['admin を外す', 'op-revoke-admin', 'revokeAdmin'],
  ] as const)('%s: 確認を開いた後に別の利用者の詳細が届いても、送るのは確認した利用者', async (_label, button, op) => {
    await mountView(adminDetail())

    await click(button)
    expect(document.body.textContent).toContain('admin@example.com')
    holder.users.detail = otherDetail()
    await flushPromises()
    await click('confirm-ok')

    expect(holder.users[op]).toHaveBeenCalledTimes(1)
    expect(holder.users[op]).toHaveBeenCalledWith(USER_ID)
  })

  it('入力のある確認でも、開いた時点の利用者に送る', async () => {
    await mountView(adminDetail())

    await click('op-suspend')
    holder.users.detail = otherDetail()
    await flushPromises()
    const submit = document.querySelector('[data-test="suspend-submit"]') as HTMLElement | null
    submit?.click()
    await flushPromises()

    expect(holder.users.suspend.mock.calls.map(([id]) => id)).not.toContain(OTHER_ID)
  })

  it('別の利用者へ移ると、前の利用者の詳細と操作を出さず、読み込み中を出す', async () => {
    holder.users.fetchDetail.mockImplementation(async () => {
      holder.users.detailLoading = true
    })
    await mountView(adminDetail())
    expect(exists('op-suspend')).toBe(true)

    await router.push(`/admin-users/users/${OTHER_ID}`)
    await flushPromises()

    expect(holder.users.fetchDetail).toHaveBeenLastCalledWith(OTHER_ID)
    expect(exists('op-suspend')).toBe(false)
    expect(exists('op-temporary-password')).toBe(false)
    expect(document.body.textContent).not.toContain('admin@example.com')
    expect(exists('detail-loading')).toBe(true)
  })

  it('開いていた確認は、別の利用者へ移ると閉じて実行しない', async () => {
    await mountView(adminDetail())
    await click('op-temporary-password')

    await router.push(`/admin-users/users/${OTHER_ID}`)
    holder.users.detail = otherDetail()
    await flushPromises()
    const ok = document.querySelector('[data-test="confirm-ok"]') as HTMLElement | null
    ok?.click()
    await flushPromises()

    expect(holder.users.issueTemporaryPassword).not.toHaveBeenCalled()
  })

  it('詳細の読み込み中は操作のボタンを押せない', async () => {
    holder.users.detailLoading = true
    await mountView(adminDetail())

    for (const id of ['op-profile', 'op-email', 'op-suspend', 'op-reinvite', 'op-temporary-password']) {
      expect((el(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
  })
})

describe('UserDetailView: 通信機の割り当て', () => {
  it('割り当ての履歴と利用期間へのリンクを表示し、終わった割り当てを薄く出す', async () => {
    const wrapper = await mountView(makeDetail({ assignments: [
      {
        account: '00000001', org_name: '組織A', device_period_id: 'period-1', assignment_id: 'assignment-1',
        valid_from: '2026-01-01T01:00:00Z', valid_to: '2026-01-01T02:00:00Z',
      },
      {
        account: '00000002', org_name: '組織B', device_period_id: 'period-2', assignment_id: 'assignment-2',
        valid_from: '2026-01-01T01:00:00Z', valid_to: null,
      },
    ] }))
    expect(wrapper.text()).toContain('00000001')
    expect(wrapper.text()).toContain('10:00')
    expect(wrapper.text()).toContain('利用期間の終わりまで')
    expect(el('user-assignment-assignment-1').classList.contains('text-disabled')).toBe(true)
    expect(el('user-assignment-assignment-2').classList.contains('text-disabled')).toBe(false)
    expect(el('user-period-period-1').getAttribute('href')).toBe('/admin-users/device-periods/period-1')
  })
})
