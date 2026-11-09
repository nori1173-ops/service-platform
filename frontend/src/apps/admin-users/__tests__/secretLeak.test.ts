import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import { adminUsersApi } from '../api'
import { useUsersStore } from '../stores/users'
import type { UserDetail, UserSummary } from '../types'
import UserDetailView from '../views/UserDetailView.vue'
import UsersView from '../views/UsersView.vue'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

vi.mock('../api', () => ({
  adminUsersApi: {
    listUsers: vi.fn(),
    getUser: vi.fn(),
    invite: vi.fn(),
    updateProfile: vi.fn(),
    changeEmail: vi.fn(),
    suspend: vi.fn(),
    restore: vi.fn(),
    reinvite: vi.fn(),
    activateAdmin: vi.fn(),
    issueTemporaryPassword: vi.fn(),
    revokeAdmin: vi.fn(),
  },
}))

// 一時パスワードの値。これがどこかに漏れたら失敗する (計画 8 章のリスク 10、レビューの Important 2)
const SECRET = 'LeakCheckSecret7Zq9Wx2'
const SELF_ID = '01JBEXAMPLE0000000000000001'
const USER_ID = '01JBEXAMPLE0000000000000002'
const CONSOLE_METHODS = ['log', 'info', 'warn', 'error', 'debug', 'trace'] as const

function adminSummary(): UserSummary {
  return {
    user_id: USER_ID,
    email: 'admin@example.com',
    status: 'active',
    kind: 'employee',
    role: 'admin',
    org_name: null,
    admin_state: 'active',
    cognito_status: 'CONFIRMED',
    cognito_enabled: true,
    created_at: '2026-11-04T01:00:00Z',
    updated_at: '2026-11-04T02:00:00Z',
    in_ledger: true,
  }
}

function adminDetail(): UserDetail {
  return {
    ...adminSummary(),
    contact: null,
    note: null,
    is_admin: true,
    totp: true,
    factors: { password: true, web_authn: false, email_otp: true },
    mfa_enabled_at: '2026-11-04T03:00:00Z',
    admin_verified_at: null,
    admin_verified_method: null,
    restore_requires: null,
    reinvite_in_progress: false,
    email_pending: null,
    reinvite_count: 0,
    suspended_at: null,
    suspend_reason: null,
    assignments: [],
  }
}

/** 循環する値 (Vue の警告の引数など) も文字列にして、秘密の値を探す */
function text(value: unknown): string {
  const seen = new WeakSet<object>()
  try {
    return JSON.stringify(value, (_key, item: unknown) => {
      if (item && typeof item === 'object') {
        if (seen.has(item)) {
          return '[circular]'
        }
        seen.add(item)
      }
      return typeof item === 'function' ? String(item) : item
    }) ?? String(value)
  } catch {
    return String(value)
  }
}

function storageEntries(storage: Storage): string[] {
  const entries: string[] = []
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index) ?? ''
    entries.push(`${key}=${storage.getItem(key) ?? ''}`)
  }
  return entries
}

let router: Router
let visited: string[]
let consoleSpies: MockInstance[]
let setItem: MockInstance

function el(id: string): HTMLElement {
  const found = document.querySelector(`[data-test="${id}"]`) as HTMLElement | null
  if (!found) {
    throw new Error(`[data-test="${id}"] が見つかりません`)
  }
  return found
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

async function mountAt(path: string): Promise<void> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin-users', name: 'admin-users.users', component: UsersView },
      { path: '/admin-users/users/:userId', name: 'admin-users.user', component: UserDetailView, props: true },
    ],
  })
  router.afterEach((to) => {
    visited.push(to.fullPath)
  })
  await router.push(path)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Host = {
    template: '<RouterView v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></RouterView>',
  }
  mount(Host, { global: { plugins: [vuetify, router] }, attachTo: document.body })
  await flushPromises()
}

/** 画面に 1 回だけ出たことを確かめ、閉じる */
async function showOnceAndClose(): Promise<void> {
  expect(el('one-time-secret').textContent).toContain(SECRET)
  await click('one-time-close')
}

/** 秘密の値が出てはいけない先を全部見て、出ていた先の名前を返す */
function leaks(notify: MockInstance): string[] {
  const ui = useUiStore()
  const targets: Record<string, unknown> = {
    'localStorage.setItem / sessionStorage.setItem': setItem.mock.calls,
    localStorage: storageEntries(localStorage),
    sessionStorage: storageEntries(sessionStorage),
    'location.href': window.location.href,
    'router (URL・query)': visited,
    'ui.notify': notify.mock.calls,
    'ui.snackbar': ui.snackbar,
    'document.title': document.title,
    'users ストア': useUsersStore().$state,
    'ui ストア': ui.$state,
    'DOM (閉じた後)': document.body.innerHTML,
  }
  CONSOLE_METHODS.forEach((method, index) => {
    targets[`console.${method}`] = consoleSpies[index].mock.calls
  })
  return Object.entries(targets)
    .filter(([, value]) => text(value).includes(SECRET))
    .map(([name]) => name)
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  sessionStorage.clear()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  visited = []
  consoleSpies = CONSOLE_METHODS.map((method) => vi.spyOn(console, method).mockImplementation(() => {}))
  setItem = vi.spyOn(Storage.prototype, 'setItem')
  vi.mocked(adminUsersApi.listUsers).mockResolvedValue({ users: [adminSummary()], self_user_id: SELF_ID })
  vi.mocked(adminUsersApi.getUser).mockResolvedValue(adminDetail())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('一時パスワードを画面の外へ出さない', () => {
  it('admin の招待', async () => {
    vi.mocked(adminUsersApi.invite).mockResolvedValue({
      user_id: USER_ID,
      email: 'admin@example.com',
      kind: 'employee',
      role: 'admin',
      status: 'active',
      admin_state: 'pending',
      temporary_password: SECRET,
    })
    await mountAt('/admin-users')
    const notify = vi.spyOn(useUiStore(), 'notify')

    await click('invite-open')
    await type('invite-email', 'admin@example.com')
    await click('invite-kind-employee')
    ;(el('invite-admin').querySelector('input') as HTMLInputElement).click()
    await flushPromises()
    await click('invite-submit')
    await showOnceAndClose()

    expect(adminUsersApi.invite).toHaveBeenCalled()
    expect(leaks(notify)).toEqual([])
  })

  it('admin の再招待', async () => {
    vi.mocked(adminUsersApi.reinvite).mockResolvedValue({
      user_id: USER_ID,
      email: 'admin@example.com',
      status: 'suspended',
      temporary_password: SECRET,
    })
    await mountAt(`/admin-users/users/${USER_ID}`)
    const notify = vi.spyOn(useUiStore(), 'notify')

    await click('op-reinvite')
    await type('reinvite-reason', 'TOTP の紛失')
    await click('reinvite-submit')
    await showOnceAndClose()

    expect(adminUsersApi.reinvite).toHaveBeenCalled()
    expect(leaks(notify)).toEqual([])
  })

  it('一時パスワードの発行', async () => {
    vi.mocked(adminUsersApi.issueTemporaryPassword).mockResolvedValue({
      user_id: USER_ID,
      temporary_password: SECRET,
    })
    await mountAt(`/admin-users/users/${USER_ID}`)
    const notify = vi.spyOn(useUiStore(), 'notify')

    await click('op-temporary-password')
    await click('confirm-ok')
    await showOnceAndClose()

    expect(adminUsersApi.issueTemporaryPassword).toHaveBeenCalled()
    expect(leaks(notify)).toEqual([])
  })
})
