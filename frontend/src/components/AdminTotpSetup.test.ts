import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { toJaMessage } from '@/utils/authErrors'
import AdminTotpSetup from './AdminTotpSetup.vue'
import TotpEnroll from './TotpEnroll.vue'

interface AuthMock {
  isLoading: boolean
  error: string | null
  beginTotpSetup: Mock
  confirmTotpSetup: Mock
  totpEnabled: Mock
  logout: Mock
}

interface MeMock {
  registerTotp: Mock
}

const holder = vi.hoisted(() => ({
  auth: null as unknown as AuthMock,
  me: null as unknown as MeMock,
}))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    isLoading: false,
    error: null,
    beginTotpSetup: vi.fn(),
    confirmTotpSetup: vi.fn(),
    totpEnabled: vi.fn(),
    logout: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

vi.mock('@/stores/me', async () => {
  const { reactive } = await import('vue')
  holder.me = reactive({ registerTotp: vi.fn() }) as MeMock
  return { useMeStore: () => holder.me }
})

const SETUP_URI = 'otpauth://totp/SERVICE-PLATFORM:admin@example.com?secret=JBSWY3DPEHPK3PXP'
const SHARED_SECRET = 'JBSWY3DPEHPK3PXP'
const StubView = { template: '<div />' }

let router: Router

async function mountSetup(): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/account', name: 'account', component: StubView },
      { path: '/login/admin', name: 'login-admin', component: StubView },
    ],
  })
  await router.push('/account')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(AdminTotpSetup, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

async function startAndEnroll(wrapper: VueWrapper, code = '123456'): Promise<void> {
  await wrapper.find('[data-test="totp-start"]').trigger('click')
  await flushPromises()
  const enroll = wrapper.findComponent(TotpEnroll)
  await enroll.find('input').setValue(code)
  await enroll.find('form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  holder.auth.isLoading = false
  holder.auth.error = null
  holder.auth.beginTotpSetup.mockReset().mockResolvedValue({
    setupUri: SETUP_URI,
    sharedSecret: SHARED_SECRET,
  })
  holder.auth.confirmTotpSetup.mockReset().mockResolvedValue(undefined)
  holder.auth.totpEnabled.mockReset().mockResolvedValue(false)
  holder.auth.logout.mockReset().mockResolvedValue(undefined)
  holder.me.registerTotp.mockReset().mockResolvedValue(undefined)
})

describe('AdminTotpSetup', () => {
  it('始めるまで QR コードを出さない', async () => {
    const wrapper = await mountSetup()

    expect(wrapper.find('[data-test="totp-start"]').exists()).toBe(true)
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)
    expect(holder.auth.beginTotpSetup).not.toHaveBeenCalled()
  })

  it('始めると setUpTOTP の QR コードとキーを出す', async () => {
    const wrapper = await mountSetup()

    await wrapper.find('[data-test="totp-start"]').trigger('click')
    await flushPromises()

    const enroll = wrapper.findComponent(TotpEnroll)
    expect(enroll.props('setupUri')).toBe(SETUP_URI)
    expect(enroll.text()).toContain(SHARED_SECRET)
  })

  // ADR D9: setUpTOTP → verifyTOTPSetup → updateMFAPreference → 「登録を終えた」API → サインアウト
  it('コードが通ったら「登録を終えた」を知らせ、サインアウトして管理者のサインインへ移る', async () => {
    const wrapper = await mountSetup()

    await startAndEnroll(wrapper)

    expect(holder.auth.confirmTotpSetup).toHaveBeenCalledWith('123456')
    expect(holder.me.registerTotp).toHaveBeenCalledTimes(1)
    expect(holder.auth.logout).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('login-admin')
    expect(router.currentRoute.value.query.registered).toBe('1')
  })

  it('コードが違えば知らせず、文言を出して入力を続けられる', async () => {
    holder.auth.confirmTotpSetup.mockImplementation(async () => {
      holder.auth.error = '認証コードが正しくありません'
      throw new Error('EnableSoftwareTokenMFAException')
    })
    const wrapper = await mountSetup()

    await startAndEnroll(wrapper, '000000')

    expect(holder.me.registerTotp).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="totp-error"]').text()).toContain('認証コードが正しくありません')
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(true)
  })

  it('CodeMismatchException の文言にはメールを含めず、認証アプリのコード入力を続けられる', async () => {
    const error = Object.assign(new Error('Invalid code'), { name: 'CodeMismatchException' })
    holder.auth.confirmTotpSetup.mockImplementation(async () => {
      holder.auth.error = toJaMessage(error)
      throw error
    })
    const wrapper = await mountSetup()

    await startAndEnroll(wrapper, '000000')

    const message = wrapper.find('[data-test="totp-error"]').text()
    expect(message).toBe('確認コードが違います。もう一度お確かめください')
    expect(message).not.toContain('メール')
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(true)
    expect(holder.me.registerTotp).not.toHaveBeenCalled()
  })

  it('知らせるのに失敗したら、サインアウトせずにやり直しのボタンを出す', async () => {
    holder.me.registerTotp
      .mockRejectedValueOnce(new Error('一時的に処理できませんでした'))
      .mockResolvedValueOnce(undefined)
    const wrapper = await mountSetup()

    await startAndEnroll(wrapper)

    expect(holder.auth.logout).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="totp-error"]').text()).toContain('一時的に処理できませんでした')
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)

    await wrapper.find('[data-test="totp-notify"]').trigger('click')
    await flushPromises()

    expect(holder.me.registerTotp).toHaveBeenCalledTimes(2)
    expect(holder.auth.confirmTotpSetup).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('login-admin')
  })

  // Cognito で TOTP が済んでいて台帳が準備中のまま (前回の知らせが失敗した) なら、知らせるだけにする
  it('Cognito で TOTP が有効なら、登録をやり直さず知らせるボタンを出す', async () => {
    holder.auth.totpEnabled.mockResolvedValue(true)
    const wrapper = await mountSetup()

    expect(wrapper.find('[data-test="totp-start"]').exists()).toBe(false)
    await wrapper.find('[data-test="totp-notify"]').trigger('click')
    await flushPromises()

    expect(holder.auth.beginTotpSetup).not.toHaveBeenCalled()
    expect(holder.me.registerTotp).toHaveBeenCalledTimes(1)
  })

  it('済んだら QR コードとキーを画面から消す', async () => {
    const wrapper = await mountSetup()

    await startAndEnroll(wrapper)

    expect(wrapper.text()).not.toContain(SHARED_SECRET)
  })
})
