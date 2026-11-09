import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { confirmSignIn, signIn, type SignInOutput } from 'aws-amplify/auth'
import { useAuthStore } from '@/stores/auth'
import LoginView from './LoginView.vue'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const EMAIL_CODE: SignInOutput = {
  isSignedIn: false,
  nextStep: {
    signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE',
    codeDeliveryDetails: { deliveryMedium: 'EMAIL', destination: 'u***@example.com' },
  },
}

enableAutoUnmount(afterEach)

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  vi.mocked(signIn).mockResolvedValue(EMAIL_CODE)
  vi.mocked(confirmSignIn).mockResolvedValue(EMAIL_CODE)
})

async function mountEmailCode(): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/login/admin', name: 'login-admin', component: { template: '<div />' } },
      { path: '/account', name: 'account', component: { template: '<div />' } },
    ],
  })
  await router.push('/login')
  await router.isReady()
  const pinia = createPinia()
  const wrapper = mount(LoginView, {
    attachTo: document.body,
    global: { plugins: [pinia, router, createVuetify({ components, directives })] },
  })
  await flushPromises()
  await wrapper.get('input[type="email"]').setValue('user@example.com')
  await wrapper.get('[data-test="start-email-code"]').trigger('click')
  await flushPromises()
  expect(useAuthStore(pinia).pending?.kind).toBe('email-code')
  return wrapper
}

async function startResend(wrapper: VueWrapper): Promise<() => Promise<void>> {
  let deliver!: (result: SignInOutput) => void
  vi.mocked(signIn).mockReturnValueOnce(new Promise((resolve) => { deliver = resolve }))
  await wrapper.get('[data-test="resend-code"]').trigger('click')
  await flushPromises()
  expect(signIn).toHaveBeenCalledTimes(2)
  return async () => {
    deliver(EMAIL_CODE)
    await flushPromises()
  }
}

describe('LoginView と認証ストア: 送り直し中のコード送信', () => {
  it('通信中はEnter相当のフォーム送信でもconfirmSignInを呼ばず、完了後は送信できる', async () => {
    const wrapper = await mountEmailCode()
    const finish = await startResend(wrapper)
    const input = wrapper.get<HTMLInputElement>('input[autocomplete="one-time-code"]')
    input.element.value = '12345678'
    input.element.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    await wrapper.get('[data-test="email-code-form"]').trigger('submit')
    await flushPromises()

    try {
      expect(confirmSignIn).not.toHaveBeenCalled()
    } finally {
      await finish()
    }

    await input.setValue('87654321')
    await wrapper.get('[data-test="email-code-form"]').trigger('submit')
    await flushPromises()
    expect(confirmSignIn).toHaveBeenCalledExactlyOnceWith({ challengeResponse: '87654321' })
  })

  it('コードの検証を待つ間に送り直しが始まったときもconfirmSignInを呼ばない', async () => {
    const wrapper = await mountEmailCode()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345678')
    let validate!: (result: { valid: boolean }) => void
    const validation = new Promise<{ valid: boolean }>((resolve) => { validate = resolve })
    wrapper.getComponent<typeof components.VForm>('[data-test="email-code-form"]').vm.$emit(
      'submit', Object.assign(validation, { preventDefault: vi.fn() }),
    )
    const finish = await startResend(wrapper)
    validate({ valid: true })
    await flushPromises()

    try {
      expect(confirmSignIn).not.toHaveBeenCalled()
    } finally {
      await finish()
    }
  })
})
