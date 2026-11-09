import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { signIn, type SignInOutput } from 'aws-amplify/auth'
import { useAuthStore } from '@/stores/auth'
import AdminLoginView from './AdminLoginView.vue'
import LoginView from './LoginView.vue'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const EMAIL = 'admin@example.com'
const StubView = { template: '<div />' }

let router: Router

function selection(factors: string[]): SignInOutput {
  return {
    isSignedIn: false,
    nextStep: { signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION', availableChallenges: factors },
  } as unknown as SignInOutput
}

/** 2 つの入口を実際のルーターで行き来する (リンクの href だけでは途中状態の持ち越しを見られない) */
async function mountAt(path: string): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: LoginView },
      { path: '/login/admin', name: 'login-admin', component: AdminLoginView },
    ],
  })
  await router.push(path)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
  localStorage.clear()
})

describe('サインインの入口の切り替え', () => {
  // TOTP を持つ admin が /login で案内に従って /login/admin へ移る (レビューの Important 1)
  it('/login で候補がパスワードだけになった後に /login/admin へ移ると、パスワードの入力が出る', async () => {
    vi.mocked(signIn).mockResolvedValue(selection(['PASSWORD', 'PASSWORD_SRP']))
    const wrapper = await mountAt('/login')
    await wrapper.find('input[type="email"]').setValue(EMAIL)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const notice = wrapper.find('[data-test="password-only"]')
    expect(notice.exists()).toBe(true)

    await notice.find('a').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login-admin')
    expect(wrapper.find('[data-test="not-password-user"]').exists()).toBe(false)
    expect(wrapper.find('input[autocomplete="current-password"]').isVisible()).toBe(true)
    expect(useAuthStore().pending).toBeNull()
  })

  it('/login/admin でパスワードの利用者でなかった後に /login へ移ると、メールアドレスの入力から始まる', async () => {
    vi.mocked(signIn).mockResolvedValue(selection(['EMAIL_OTP']))
    const wrapper = await mountAt('/login/admin')
    await wrapper.find('input[type="email"]').setValue('customer@example.com')
    await wrapper.find('input[autocomplete="current-password"]').setValue('Abcdefghij12')
    await wrapper.find('[data-test="password-form"]').trigger('submit')
    await flushPromises()
    const notice = wrapper.find('[data-test="not-password-user"]')
    expect(notice.exists()).toBe(true)

    await notice.find('a').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper.find('[data-test="factor-EMAIL_OTP"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="start-passkey"]').isVisible()).toBe(true)
    expect(useAuthStore().pending).toBeNull()
    expect(useAuthStore().signInEmail).toBeNull()
  })
})
