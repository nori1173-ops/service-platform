import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Pending } from '@/stores/auth'
import { toJaMessage } from '@/utils/authErrors'
import AdminLoginView from './AdminLoginView.vue'

interface AuthMock {
  email: string | null
  isLoading: boolean
  error: string | null
  pending: Pending
  isAuthenticated: boolean
  startAdminSignIn: Mock
  submitTotp: Mock
  submitNewPassword: Mock
  cancelSignIn: Mock
}

const holder = vi.hoisted(() => ({ auth: null as unknown as AuthMock }))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    email: null,
    isLoading: false,
    error: null,
    pending: null,
    isAuthenticated: false,
    startAdminSignIn: vi.fn(),
    submitTotp: vi.fn(),
    submitNewPassword: vi.fn(),
    cancelSignIn: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

const EMAIL = 'admin@example.com'
const LAST_EMAIL_KEY = 'service-platform:last-email'
const PASSWORD = 'Abcdefghij12'
const StubView = { template: '<div />' }

let router: Router

async function mountView(fullPath = '/login/admin'): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: StubView },
      { path: '/login/admin', name: 'login-admin', component: StubView },
      { path: '/admin-users', name: 'admin-users.users', component: StubView },
    ],
  })
  await router.push(fullPath)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(AdminLoginView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

function byTest(wrapper: VueWrapper, id: string): DOMWrapper<Element> {
  return wrapper.find(`[data-test="${id}"]`)
}

function done(): void {
  holder.auth.pending = null
  holder.auth.email = EMAIL
  holder.auth.isAuthenticated = true
}

async function submitPassword(wrapper: VueWrapper, password = PASSWORD): Promise<void> {
  await wrapper.find('input[type="email"]').setValue(EMAIL)
  await wrapper.find('input[autocomplete="current-password"]').setValue(password)
  await byTest(wrapper, 'password-form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  localStorage.clear()
  Object.assign(holder.auth, {
    email: null,
    isLoading: false,
    error: null,
    pending: null,
    isAuthenticated: false,
  })
  for (const fn of [
    holder.auth.startAdminSignIn,
    holder.auth.submitTotp,
    holder.auth.submitNewPassword,
    holder.auth.cancelSignIn,
  ]) {
    fn.mockReset()
  }
})

afterEach(() => {
  localStorage.clear()
})

describe('AdminLoginView', () => {
  it.each([EMAIL, 'member+alerts&news@example.com', 'member%26team@example.com', 'member=name@example.com'])(
    '招待リンクのメールアドレス %s を入力欄に入れる',
    async (email) => {
      const wrapper = await mountView('/login/admin#email=' + encodeURIComponent(email))

      expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(email)
      expect(holder.auth.startAdminSignIn).not.toHaveBeenCalled()
      expect(localStorage.getItem(LAST_EMAIL_KEY)).toBeNull()
    },
  )

  it('招待リンクのメールアドレスを使い、保存値を読まず、書き換えない', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'remembered@example.com')

    const wrapper = await mountView('/login/admin#email=' + encodeURIComponent(EMAIL))

    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(EMAIL)
    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBe('remembered@example.com')
  })

  it.each(['', 'not-an-email', '@example.com', 'user@', 'user@example', 'user name@example.com', 'user@example.com\n'])(
    '招待リンクの形が正しくない値 %j は無視し、hash を消す',
    async (email) => {
      const wrapper = await mountView('/login/admin#email=' + encodeURIComponent(email))

      expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe('')
      expect(router.currentRoute.value.hash).toBe('')
      expect(localStorage.getItem(LAST_EMAIL_KEY)).toBeNull()
    },
  )

  it('招待リンクを読んだ後は query を残して hash を消す', async () => {
    await mountView('/login/admin?redirect=%2Fadmin-users&registered=1#email=' + encodeURIComponent(EMAIL))

    expect(router.currentRoute.value.hash).toBe('')
    expect(router.currentRoute.value.query).toEqual({ redirect: '/admin-users', registered: '1' })
  })

  it('メールアドレス以外の hash はそのままにする', async () => {
    await mountView('/login/admin#help')

    expect(router.currentRoute.value.hash).toBe('#help')
  })

  it('招待リンクで補完されたアドレスをサインインに使う', async () => {
    const wrapper = await mountView('/login/admin#email=' + encodeURIComponent(EMAIL))

    await wrapper.find('input[autocomplete="current-password"]').setValue(PASSWORD)
    await byTest(wrapper, 'password-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.startAdminSignIn).toHaveBeenCalledWith(EMAIL, PASSWORD)
    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBeNull()
  })

  it('メールアドレスとパスワードでサインインを始める', async () => {
    const wrapper = await mountView()

    await submitPassword(wrapper)

    expect(holder.auth.startAdminSignIn).toHaveBeenCalledWith(EMAIL, PASSWORD)
  })

  it('パスワードが空なら始めない', async () => {
    const wrapper = await mountView()

    await submitPassword(wrapper, '')

    expect(holder.auth.startAdminSignIn).not.toHaveBeenCalled()
  })

  // パスワードは画面のメモリだけで使い、送ったら入力欄から消す
  it('送った後はパスワードの入力欄を空にする', async () => {
    const wrapper = await mountView()
    holder.auth.startAdminSignIn.mockImplementation(async () => {
      holder.auth.pending = { kind: 'totp-code' }
    })

    await submitPassword(wrapper)

    const input = wrapper.find('input[autocomplete="current-password"]')
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('TOTP のコードを送り、済んだら redirect の先へ進む', async () => {
    const wrapper = await mountView('/login/admin?redirect=%2Fadmin-users')
    holder.auth.pending = { kind: 'totp-code' }
    await flushPromises()
    holder.auth.submitTotp.mockImplementation(async () => done())

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
    await byTest(wrapper, 'totp-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitTotp).toHaveBeenCalledWith('123456')
    expect(router.currentRoute.value.fullPath).toBe('/admin-users')
  })

  it('TOTP のコード不一致にはメールを含まない文言を出し、入力を続けられる', async () => {
    const wrapper = await mountView()
    holder.auth.pending = { kind: 'totp-code' }
    const error = Object.assign(new Error('Invalid code'), { name: 'CodeMismatchException' })
    holder.auth.submitTotp.mockImplementation(async () => {
      holder.auth.error = toJaMessage(error)
      throw error
    })
    await flushPromises()

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('000000')
    await byTest(wrapper, 'totp-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitTotp).toHaveBeenCalledWith('000000')
    expect(wrapper.find('.v-alert').text()).toBe('確認コードが違います。もう一度お確かめください')
    expect(wrapper.find('.v-alert').text()).not.toContain('メール')
    expect(byTest(wrapper, 'totp-form').exists()).toBe(true)
    expect(router.currentRoute.value.path).toBe('/login/admin')
  })

  it('6 桁でない TOTP のコードは送らない', async () => {
    const wrapper = await mountView()
    holder.auth.pending = { kind: 'totp-code' }
    await flushPromises()

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345')
    await byTest(wrapper, 'totp-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitTotp).not.toHaveBeenCalled()
  })

  it('一時パスワードのときは新しいパスワードを 2 回入れて決める', async () => {
    const wrapper = await mountView()
    holder.auth.pending = { kind: 'new-password' }
    await flushPromises()

    const inputs = wrapper.findAll('input[autocomplete="new-password"]')
    expect(inputs).toHaveLength(2)
    await inputs[0].setValue(PASSWORD)
    await inputs[1].setValue(PASSWORD)
    await byTest(wrapper, 'new-password-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitNewPassword).toHaveBeenCalledWith(PASSWORD)
  })

  it.each([
    ['方針 (12 文字) を満たさない', 'Short1', 'Short1'],
    ['確認が合わない', PASSWORD, `${PASSWORD}x`],
  ])('新しいパスワードが%sときは送らない', async (_label, first, second) => {
    const wrapper = await mountView()
    holder.auth.pending = { kind: 'new-password' }
    await flushPromises()

    const inputs = wrapper.findAll('input[autocomplete="new-password"]')
    await inputs[0].setValue(first)
    await inputs[1].setValue(second)
    await byTest(wrapper, 'new-password-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitNewPassword).not.toHaveBeenCalled()
  })

  // パスワードを持たない利用者 (お客様) は管理者のサインインでは入れない。行き止まりにしない
  it.each<Pending>([
    { kind: 'select-factor', factors: ['EMAIL_OTP'] },
    { kind: 'email-code' },
  ])('パスワードの利用者でなければ通常のサインインへ案内する (%o)', async (pending) => {
    const wrapper = await mountView()
    holder.auth.pending = pending
    await flushPromises()

    const notice = byTest(wrapper, 'not-password-user')
    expect(notice.exists()).toBe(true)
    expect(notice.find('a').attributes('href')).toBe('/login')
  })

  it('TOTP の登録を終えてサインアウトされたときの案内を出す', async () => {
    const wrapper = await mountView('/login/admin?registered=1')

    expect(byTest(wrapper, 'registered-notice').text()).toContain('確認待ち')
  })

  it('失敗の文言を出す', async () => {
    const wrapper = await mountView()
    holder.auth.error = 'メールアドレスまたはパスワードが正しくありません'
    await flushPromises()

    expect(wrapper.text()).toContain('メールアドレスまたはパスワードが正しくありません')
  })

  it('「最初からやり直す」でパスワードの入力へ戻れる', async () => {
    const wrapper = await mountView()
    holder.auth.cancelSignIn.mockImplementation(() => {
      holder.auth.pending = null
    })
    holder.auth.pending = { kind: 'totp-code' }
    await flushPromises()

    await byTest(wrapper, 'restart').trigger('click')
    await flushPromises()

    expect(holder.auth.cancelSignIn).toHaveBeenCalled()
    expect(wrapper.find('input[autocomplete="current-password"]').isVisible()).toBe(true)
  })

  it('通常のサインインへのリンクがある', async () => {
    const wrapper = await mountView()

    expect(byTest(wrapper, 'customer-login-link').attributes('href')).toBe('/login')
  })

  // admin のパスワードの変更・回復は別の admin が一時パスワードを出す (判断 15)
  it('パスワードの再設定の導線を出さない', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).not.toContain('パスワードをお忘れ')
  })
})


it('登録済みのパスキーで通常の入口から入れることを案内する', async () => {
  const wrapper = await mountView()
  const guide = wrapper.get('[data-test="passkey-login-guide"]')
  expect(guide.text()).toContain('パスキーを登録済みの方は')
  expect(guide.get('a').attributes('href')).toBe('/login')
})
