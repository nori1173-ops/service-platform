import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Pending } from '@/stores/auth'
import { toJaMessage } from '@/utils/authErrors'
import LoginView from './LoginView.vue'

interface AuthMock {
  email: string | null
  groups: string[]
  isLoading: boolean
  error: string | null
  pending: Pending
  isAuthenticated: boolean
  isAdmin: boolean
  passkeyFailed: boolean
  availableFactors: string[] | null
  passwordOnly: boolean
  lastFactor: string | null
  startSignIn: Mock
  selectFactor: Mock
  submitEmailCode: Mock
  resendEmailCode: Mock
  cancelSignIn: Mock
}

interface PasskeysMock {
  items: unknown[]
  loaded: boolean
  fetch: Mock
}

const holder = vi.hoisted(() => ({
  auth: null as unknown as AuthMock,
  passkeys: null as unknown as PasskeysMock,
}))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    email: null,
    groups: [],
    isLoading: false,
    error: null,
    pending: null,
    isAuthenticated: false,
    isAdmin: false,
    passkeyFailed: false,
    availableFactors: ['WEB_AUTHN', 'EMAIL_OTP'],
    passwordOnly: false,
    lastFactor: null,
    startSignIn: vi.fn(),
    selectFactor: vi.fn(),
    submitEmailCode: vi.fn(),
    resendEmailCode: vi.fn(),
    cancelSignIn: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

vi.mock('@/stores/passkeys', async () => {
  const { reactive } = await import('vue')
  holder.passkeys = reactive({ items: [], loaded: false, fetch: vi.fn() }) as PasskeysMock
  return { usePasskeysStore: () => holder.passkeys }
})

const EMAIL = 'user@example.com'
const LAST_EMAIL_KEY = 'service-platform:last-email'
const StubView = { template: '<div />' }

let router: Router

enableAutoUnmount(afterEach)

async function mountLogin(fullPath = '/login'): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: StubView },
      { path: '/login/admin', name: 'login-admin', component: StubView },
      { path: '/account', name: 'account', component: StubView },
      { path: '/sample-billing/settings', name: 'billing-settings', component: StubView },
    ],
  })
  await router.push(fullPath)
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(LoginView, { attachTo: document.body, global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

function byTest(wrapper: VueWrapper, id: string): DOMWrapper<Element> {
  return wrapper.find(`[data-test="${id}"]`)
}

function signedInBy(factor: string): void {
  holder.auth.pending = null
  holder.auth.email = EMAIL
  holder.auth.isAuthenticated = true
  holder.auth.lastFactor = factor
}

async function submitEmail(wrapper: VueWrapper, value = EMAIL): Promise<void> {
  await wrapper.find('input[type="email"]').setValue(value)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

function namedError(name: string, message: string): Error {
  return Object.assign(new Error(message), { name })
}

async function openEmailCode(wrapper: VueWrapper): Promise<void> {
  holder.auth.startSignIn.mockImplementation(async () => {
    holder.auth.pending = { kind: 'email-code', destination: 'u***@example.com' }
  })
  await wrapper.find('input[type="email"]').setValue(EMAIL)
  await byTest(wrapper, 'start-email-code').trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 5, 9, 5))
  localStorage.clear()
  Object.assign(holder.auth, {
    email: null,
    groups: [],
    isLoading: false,
    error: null,
    pending: null,
    isAuthenticated: false,
    isAdmin: false,
    passkeyFailed: false,
    availableFactors: ['WEB_AUTHN', 'EMAIL_OTP'],
    passwordOnly: false,
    lastFactor: null,
  })
  for (const fn of [
    holder.auth.startSignIn,
    holder.auth.selectFactor,
    holder.auth.submitEmailCode,
    holder.auth.resendEmailCode,
    holder.auth.cancelSignIn,
  ]) {
    fn.mockReset()
  }
  holder.auth.cancelSignIn.mockImplementation(() => {
    holder.auth.pending = null
    holder.auth.error = null
  })
  holder.passkeys.items = []
  holder.passkeys.loaded = false
  holder.passkeys.fetch.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
})

describe('LoginView', () => {
  it.each([EMAIL, 'member+alerts&news@example.com', 'member%26team@example.com', 'member=name@example.com'])(
    '招待リンクのメールアドレス %s を入力欄に入れる',
    async (email) => {
      const wrapper = await mountLogin('/login#email=' + encodeURIComponent(email))

      expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(email)
      expect(holder.auth.startSignIn).not.toHaveBeenCalled()
      expect(localStorage.getItem(LAST_EMAIL_KEY)).toBeNull()
    },
  )

  it('招待リンクのメールアドレスを前回のものより優先し、保存値はまだ変えない', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'remembered@example.com')

    const wrapper = await mountLogin('/login#email=' + encodeURIComponent(EMAIL))

    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(EMAIL)
    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBe('remembered@example.com')
  })

  it.each(['', 'not-an-email', '@example.com', 'user@', 'user@example', 'user name@example.com', 'user@example.com\n'])(
    '招待リンクの形が正しくない値 %j は無視し、hash を消す',
    async (email) => {
      const wrapper = await mountLogin('/login#email=' + encodeURIComponent(email))

      expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe('')
      expect(router.currentRoute.value.hash).toBe('')
      expect(localStorage.getItem(LAST_EMAIL_KEY)).toBeNull()
    },
  )

  it('招待リンクを読んだ後は query を残して hash を消す', async () => {
    await mountLogin('/login?redirect=%2Fsample-billing/settings&registered=1#email=' + encodeURIComponent(EMAIL))

    expect(router.currentRoute.value.hash).toBe('')
    expect(router.currentRoute.value.query).toEqual({ redirect: '/sample-billing/settings', registered: '1' })
  })

  it('メールアドレス以外の hash はそのままにする', async () => {
    await mountLogin('/login#help')

    expect(router.currentRoute.value.hash).toBe('#help')
  })

  it('招待リンクの値が正しくなければ前回のメールアドレスを使う', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'remembered@example.com')

    const wrapper = await mountLogin('/login#email=not-an-email')

    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe('remembered@example.com')
    expect(router.currentRoute.value.hash).toBe('')
  })

  it('招待リンクのアドレスはサインインが完了したときに保存する', async () => {
    const wrapper = await mountLogin('/login#email=' + encodeURIComponent(EMAIL))
    holder.auth.startSignIn.mockImplementation(async () => signedInBy('WEB_AUTHN'))

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(holder.auth.startSignIn).toHaveBeenCalledWith(EMAIL)
    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBe(EMAIL)
  })

  // パスワードの入力は出さない (ADR D1)
  it('初期状態はメールアドレスだけを入れる', async () => {
    const wrapper = await mountLogin()

    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(wrapper.find('input[autocomplete="current-password"]').exists()).toBe(false)
  })

  it('メールアドレスを送るとパスキーを第一候補にサインインを始める', async () => {
    const wrapper = await mountLogin()

    await submitEmail(wrapper)

    expect(holder.auth.startSignIn).toHaveBeenCalledWith(EMAIL)
  })

  it('形の正しくないメールアドレスでは始めない', async () => {
    const wrapper = await mountLogin()

    await submitEmail(wrapper, 'not-an-email')

    expect(holder.auth.startSignIn).not.toHaveBeenCalled()
  })

  it('「メールのコードでサインイン」でメールのコードを第一候補にする', async () => {
    const wrapper = await mountLogin()
    await wrapper.find('input[type="email"]').setValue(EMAIL)

    await byTest(wrapper, 'start-email-code').trigger('click')
    await flushPromises()

    expect(holder.auth.startSignIn).toHaveBeenCalledWith(EMAIL, 'EMAIL_OTP')
  })

  it('前回のメールアドレスを覚えていて入力欄に入れる', async () => {
    localStorage.setItem(LAST_EMAIL_KEY, 'remembered@example.com')

    const wrapper = await mountLogin()

    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(
      'remembered@example.com',
    )
  })

  it('パスキーで入れたらメールアドレスを覚え、redirect の先へ進む', async () => {
    const wrapper = await mountLogin('/login?redirect=%2Fsample-billing%2Fsettings')
    holder.auth.startSignIn.mockImplementation(async () => signedInBy('WEB_AUTHN'))

    await submitEmail(wrapper)

    expect(localStorage.getItem(LAST_EMAIL_KEY)).toBe(EMAIL)
    expect(router.currentRoute.value.fullPath).toBe('/sample-billing/settings')
    expect(holder.passkeys.fetch).not.toHaveBeenCalled()
  })

  it('外部サイトを指す redirect クエリは無視してホームへ進む', async () => {
    const wrapper = await mountLogin('/login?redirect=%2F%2Fevil.example.com')
    holder.auth.startSignIn.mockImplementation(async () => signedInBy('WEB_AUTHN'))

    await submitEmail(wrapper)

    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('候補にメールのコードがあれば選べる', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'select-factor', factors: ['EMAIL_OTP', 'PASSWORD'] }
    await flushPromises()

    expect(byTest(wrapper, 'factor-WEB_AUTHN').exists()).toBe(false)
    await byTest(wrapper, 'factor-EMAIL_OTP').trigger('click')
    await flushPromises()

    expect(holder.auth.selectFactor).toHaveBeenCalledWith('EMAIL_OTP')
  })

  // TOTP を持つ admin は候補がパスワードだけになる (PoC 5)。行き止まりにしない
  it('候補にパスワードしか無ければ管理者のサインインへ案内する', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'select-factor', factors: ['PASSWORD', 'PASSWORD_SRP'] }
    holder.auth.passwordOnly = true
    await flushPromises()

    const notice = byTest(wrapper, 'password-only')
    expect(notice.exists()).toBe(true)
    expect(notice.find('a').attributes('href')).toBe('/login/admin')
    expect(byTest(wrapper, 'factor-EMAIL_OTP').exists()).toBe(false)
  })

  it('メールのコードの入力でコードを送る', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'email-code', destination: 'u***@e***' }
    await flushPromises()

    expect(wrapper.text()).toContain('u***@e***')
    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitEmailCode).toHaveBeenCalledWith('12345678')
  })

  it.each(['12345', 'abcdefgh', '123456789'])('コード %s は送らない', async (code) => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()

    await wrapper.find('input[autocomplete="one-time-code"]').setValue(code)
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitEmailCode).not.toHaveBeenCalled()
  })

  it('コードを送り直せる', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()

    await byTest(wrapper, 'resend-code').trigger('click')
    await flushPromises()

    expect(holder.auth.resendEmailCode).toHaveBeenCalledTimes(1)
  })

  it('送った時刻と宛先、到着待ち・送り直し・期限の案内を常に出す', async () => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)

    const form = byTest(wrapper, 'email-code-form')
    expect(form.text()).toContain('09:05 に u***@example.com 宛にコードを送りました')
    expect(form.text()).toContain('メールが届くまで数分かかることがあります。届かないときは迷惑メールのフォルダもご確認ください。')
    expect(form.text()).toContain('コードを送り直すと、前のコードは使えなくなります。いちばん新しいメールのコードを入れてください。')
    expect(form.text()).toContain('09:15 までに入れてください。過ぎたら最初からやり直してください。')
    expect(wrapper.text()).not.toContain('新しいコードを送りました')
  })

  it('コードの入力画面を閉じずに別のアプリでメールを見るよう案内する', async () => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)

    const form = byTest(wrapper, 'email-code-form')
    expect(form.isVisible()).toBe(true)
    expect(form.findAll('li').map((item) => item.text())).toContain(
      'この画面を閉じずに、別のアプリでメールを見てください。閉じるとコードが無効になり、やり直しになります。',
    )
  })

  it.each([
    ['09:55', new Date(2026, 10, 5, 9, 55), '10:05'],
    ['23:55', new Date(2026, 10, 5, 23, 55), '00:05'],
  ])('送信時刻 %s から10分後の期限を時刻で出す', async (sentAt, now, deadline) => {
    vi.setSystemTime(now)
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)

    const text = byTest(wrapper, 'email-code-form').text()
    expect(text).toContain(`${sentAt} に u***@example.com 宛にコードを送りました`)
    expect(text).toContain(`${deadline} までに入れてください`)
  })

  it('認証方法の選択からコードを送ったときも時刻を出す', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'select-factor', factors: ['EMAIL_OTP'] }
    holder.auth.selectFactor.mockImplementation(async () => {
      holder.auth.pending = { kind: 'email-code', destination: 'u***@example.com' }
    })
    await flushPromises()

    vi.setSystemTime(new Date(2026, 10, 5, 9, 7))
    await byTest(wrapper, 'factor-EMAIL_OTP').trigger('click')
    await flushPromises()

    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:07 に u***@example.com 宛にコードを送りました')
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:17 までに入れてください')
  })

  it('宛先が返らなければ従来のメールアドレスの表示を保つ', async () => {
    const wrapper = await mountLogin()
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()

    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:05 に メールアドレス 宛にコードを送りました')
  })

  it('送り直し中はコード入力とサインインを無効にし、送り直しボタンだけを読み込み中にする', async () => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    let finish!: () => void
    const delivery = new Promise<void>((resolve) => { finish = resolve })
    holder.auth.resendEmailCode.mockImplementation(async () => {
      holder.auth.pending = null
      holder.auth.isLoading = true
      try {
        await delivery
        holder.auth.pending = { kind: 'email-code', destination: 'u***@example.com' }
      } finally {
        holder.auth.isLoading = false
      }
    })
    vi.setSystemTime(new Date(2026, 10, 5, 9, 8))

    await byTest(wrapper, 'resend-code').trigger('click')
    await flushPromises()

    expect(byTest(wrapper, 'email-code-form').isVisible()).toBe(true)
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:05 に u***@example.com 宛にコードを送りました')
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:15 までに入れてください')
    expect(byTest(wrapper, 'resend-code').classes()).toContain('v-btn--loading')
    const form = byTest(wrapper, 'email-code-form')
    const input = form.find('input[autocomplete="one-time-code"]')
    const signIn = form.find('button[type="submit"]')
    expect(input.attributes('disabled')).toBeDefined()
    expect(signIn.attributes('disabled')).toBeDefined()
    expect(signIn.classes()).not.toContain('v-btn--loading')
    expect(signIn.text()).toBe('サインイン')
    expect(byTest(wrapper, 'restart').attributes('disabled')).toBeDefined()
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(false)
    expect(byTest(wrapper, 'admin-login-link').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('新しいコードを送りました')

    finish()
    await flushPromises()
    expect(byTest(wrapper, 'email-code-form').isVisible()).toBe(true)
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:08 に u***@example.com 宛にコードを送りました')
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:18 までに入れてください')
    expect(wrapper.find('[role="status"]').text()).toBe('新しいコードを送りました')
    expect(byTest(wrapper, 'resend-code').classes()).not.toContain('v-btn--loading')
    expect(input.attributes('disabled')).toBeUndefined()
    expect(signIn.attributes('disabled')).toBeUndefined()
  })

  it('送り直しに成功したら時刻と入力欄を更新し、完了の案内を一時的に出す', async () => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    holder.auth.resendEmailCode.mockImplementation(async () => {
      holder.auth.pending = null
      await Promise.resolve()
      holder.auth.pending = { kind: 'email-code', destination: 'u***@example.com' }
    })
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(new Date(2026, 10, 5, 9, 8))

    await byTest(wrapper, 'resend-code').trigger('click')
    await flushPromises()

    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:08 に u***@example.com 宛にコードを送りました')
    expect((wrapper.find('input[autocomplete="one-time-code"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.find('[role="status"]').text()).toBe('新しいコードを送りました')

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('新しいコードを送りました')
  })

  it.each([
    ['CodeDeliveryFailureException', '確認コードを送信できませんでした。しばらくしてからやり直してください', 1],
    ['LimitExceededException', '試行回数の上限に達しました。しばらく時間をおいてから再試行してください', 1],
    ['TooManyRequestsException', 'アクセスが集中しています。しばらく時間をおいてから再試行してください', 1],
    ['Error', '送信できませんでした。', 0],
  ] as const)('%s で送り直しに失敗したら最初の段に戻り、理由の案内を重複させない', async (name, reason, waitCount) => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    const error = namedError(name, reason)
    holder.auth.resendEmailCode.mockImplementation(async () => {
      holder.auth.pending = null
      holder.auth.error = toJaMessage(error)
      throw error
    })
    vi.setSystemTime(new Date(2026, 10, 5, 9, 8))

    await byTest(wrapper, 'resend-code').trigger('click')
    await flushPromises()

    expect(holder.auth.pending).toBeNull()
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(EMAIL)
    expect(byTest(wrapper, 'email-code-form').isVisible()).toBe(false)
    const message = wrapper.find('[role="alert"]').text()
    expect(message).toBe(
      `送り直しに失敗したため、最初の画面に戻りました。${reason.replace(/。$/, '')}。`,
    )
    expect(message.match(/しばらく/g) ?? []).toHaveLength(waitCount)
    expect(wrapper.text()).not.toContain('新しいコードを送りました')
  })

  it.each([null, ''])('送り直しの失敗の理由が %s のときは待ってからの再送を案内する', async (reason) => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    holder.auth.resendEmailCode.mockImplementation(async () => {
      holder.auth.pending = null
      holder.auth.error = reason
      throw new Error()
    })

    await byTest(wrapper, 'resend-code').trigger('click')
    await flushPromises()

    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
    expect(wrapper.find('[role="alert"]').text()).toBe(
      '送り直しに失敗したため、最初の画面に戻りました。しばらくしてから、もう一度コードを送ってください。',
    )
  })

  it.each([
    'Invalid session for the user, session is expired.',
    'Invalid session for the user, session can only be used once.',
    'Invalid session for the user.',
  ])('セッションのエラー %s で最初の段に戻り、アドレスと案内を残す', async (message) => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    const error = namedError('NotAuthorizedException', message)
    holder.auth.submitEmailCode.mockImplementation(async () => {
      holder.auth.error = toJaMessage(error)
      throw error
    })

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.pending).toBeNull()
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(EMAIL)
    expect(wrapper.text()).toContain('サインインの手続きの期限が切れました。もう一度最初からお試しください')
    expect(wrapper.text()).not.toContain('メールアドレスまたはパスワードが正しくありません')
    expect(holder.passkeys.fetch).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it.each([
    ['CodeMismatchException', '確認コードが違います。いちばん新しいメールのコードを入れてください'],
    ['ExpiredCodeException', '確認コードの有効期限が切れています。コードを送り直してください'],
    ['NotAuthorizedException', 'メールアドレスまたはパスワードが正しくありません'],
  ])('%s では案内を出し、コードの入力を続けられる', async (name, expected) => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    const error = namedError(
      name,
      name === 'NotAuthorizedException' ? 'Incorrect username or password.' : 'Invalid code',
    )
    holder.auth.submitEmailCode.mockImplementation(async () => {
      holder.auth.error = toJaMessage(error, { emailCode: true })
      throw error
    })
    vi.setSystemTime(new Date(2026, 10, 5, 9, 8))

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(holder.auth.pending?.kind).toBe('email-code')
    expect(byTest(wrapper, 'email-code-form').isVisible()).toBe(true)
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:05 に')
    expect(byTest(wrapper, 'email-code-form').text()).toContain('09:15 までに入れてください')
    expect(wrapper.text()).toContain(expected)
  })

  it('「メールアドレスを直す」で最初の段に戻り、アドレスを残してコードを消す', async () => {
    const wrapper = await mountLogin()
    await openEmailCode(wrapper)
    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')

    expect(byTest(wrapper, 'restart').text()).toBe('メールアドレスを直す')
    await byTest(wrapper, 'restart').trigger('click')
    await flushPromises()

    expect((wrapper.find('input[type="email"]').element as HTMLInputElement).value).toBe(EMAIL)
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
    await openEmailCode(wrapper)
    expect((wrapper.find('input[autocomplete="one-time-code"]').element as HTMLInputElement).value).toBe('')
  })

  // 初回はメールのコードで入り、続けてパスキーを登録する (ADR D1)
  it('メールのコードで入ってパスキーが 0 個ならアカウントの画面で登録を促す', async () => {
    const wrapper = await mountLogin('/login?redirect=%2Fsample-billing%2Fsettings')
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()
    holder.auth.submitEmailCode.mockImplementation(async () => signedInBy('EMAIL_OTP'))
    holder.passkeys.fetch.mockImplementation(async () => {
      holder.passkeys.loaded = true
      holder.passkeys.items = []
    })

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(holder.passkeys.fetch).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('account')
  })

  it('メールのコードで入ってもパスキーがあれば redirect の先へ進む', async () => {
    const wrapper = await mountLogin('/login?redirect=%2Fsample-billing%2Fsettings')
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()
    holder.auth.submitEmailCode.mockImplementation(async () => signedInBy('EMAIL_OTP'))
    holder.passkeys.fetch.mockImplementation(async () => {
      holder.passkeys.loaded = true
      holder.passkeys.items = [{ credentialId: 'cred-1' }]
    })

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('12345678')
    await byTest(wrapper, 'email-code-form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/sample-billing/settings')
  })

  it('パスキーが使えなかったらメールのコードへ切り替えられる', async () => {
    const wrapper = await mountLogin()
    holder.auth.startSignIn.mockImplementationOnce(async () => {
      holder.auth.error = 'パスキーの確認が取り消されました'
      holder.auth.passkeyFailed = true
      throw new Error('PasskeyAuthenticationCanceled')
    })

    await submitEmail(wrapper)
    expect(wrapper.text()).toContain('パスキーの確認が取り消されました')

    await byTest(wrapper, 'fallback-email-code').trigger('click')
    await flushPromises()

    expect(holder.auth.startSignIn).toHaveBeenLastCalledWith(EMAIL, 'EMAIL_OTP')
  })

  it('途中の段から「最初からやり直す」でメールアドレスの入力へ戻れる', async () => {
    const wrapper = await mountLogin()
    holder.auth.cancelSignIn.mockImplementation(() => {
      holder.auth.pending = null
    })
    holder.auth.pending = { kind: 'email-code' }
    await flushPromises()

    await byTest(wrapper, 'restart').trigger('click')
    await flushPromises()

    expect(holder.auth.cancelSignIn).toHaveBeenCalled()
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
  })

  it('管理者のサインインへのリンクがある', async () => {
    const wrapper = await mountLogin()

    expect(byTest(wrapper, 'admin-login-link').attributes('href')).toBe('/login/admin')
  })

  // 招待制なので登録とパスワードの再設定の導線は無い (ADR D2)
  it('新規登録とパスワードの再設定の導線を出さない', async () => {
    const wrapper = await mountLogin()

    expect(wrapper.text()).not.toContain('新規登録')
    expect(wrapper.text()).not.toContain('パスワードをお忘れ')
  })
})


it('パスキー取消後、メールの候補が無ければ管理者の入口を案内する', async () => {
  const wrapper = await mountLogin()
  holder.auth.passkeyFailed = true
  holder.auth.error = 'パスキーの確認が取り消されました'
  holder.auth.availableFactors = ['WEB_AUTHN', 'PASSWORD']
  await flushPromises()
  expect(byTest(wrapper, 'fallback-email-code').exists()).toBe(false)
  expect(byTest(wrapper, 'start-email-code').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('メールのコードをお使いください')
  expect(byTest(wrapper, 'fallback-admin').get('a').attributes('href')).toBe('/login/admin')
})

it('admin がパスキーで入ったら登録を促さず通常の着地へ進む', async () => {
  const wrapper = await mountLogin()
  holder.auth.isAdmin = true
  holder.auth.startSignIn.mockImplementation(async () => signedInBy('WEB_AUTHN'))
  await submitEmail(wrapper)
  expect(router.currentRoute.value.name).toBe('home')
  expect(holder.passkeys.fetch).not.toHaveBeenCalled()
})


it('パスキー取消後、候補が不明ならメールのコードを案内する', async () => {
  const wrapper = await mountLogin()
  holder.auth.passkeyFailed = true
  holder.auth.availableFactors = null
  holder.auth.error = 'パスキーの確認が取り消されました'
  await flushPromises()
  expect(byTest(wrapper, 'fallback-email-code').exists()).toBe(true)
  expect(byTest(wrapper, 'start-email-code').exists()).toBe(true)
  expect(byTest(wrapper, 'fallback-admin').exists()).toBe(false)
})
