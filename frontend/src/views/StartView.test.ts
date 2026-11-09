import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import StartView from './StartView.vue'

const holder = vi.hoisted(() => ({ auth: null as unknown as { groups: string[] } }))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({ groups: [] as string[] })
  return { useAuthStore: () => holder.auth }
})

const Stub = { template: '<div />' }

async function mountView(): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StartView },
      { path: '/devices', name: 'devices', component: Stub },
      { path: '/account', name: 'account', component: Stub },
      { path: '/admin-users', name: 'admin-users.users', component: Stub },
    ],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(StartView, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  holder.auth.groups = []
})

// ADR D5: サインインの後に「通信機単位」と「アカウント単位」を選ぶ
describe('StartView', () => {
  it('通信機単位とアカウント単位の 2 つのカードを出す', async () => {
    const wrapper = await mountView()

    const device = wrapper.get('[data-test="start-device"]')
    const account = wrapper.get('[data-test="start-account"]')
    expect(device.text()).toContain('通信機単位')
    expect(device.attributes('href')).toBe('/devices')
    expect(account.text()).toContain('アカウント単位')
    expect(account.attributes('href')).toBe('/account')
  })

  it('admin でなければ利用者の管理へのリンクを出さない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="start-admin"]').exists()).toBe(false)
  })

  it('admin には利用者の管理へのリンクを出す', async () => {
    holder.auth.groups = ['admin']

    const wrapper = await mountView()

    const link = wrapper.get('[data-test="start-admin"]')
    expect(link.text()).toContain('利用者の管理')
    expect(link.attributes('href')).toBe('/admin-users')
  })
})
