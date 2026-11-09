import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { baseApi } from '@/services/baseApi'
import { useMeStore, type Me } from './me'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

vi.mock('@/services/baseApi', () => ({
  baseApi: { getMe: vi.fn(), registerTotp: vi.fn() },
}))

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user_id: '01JBEXAMPLE0000000000000001',
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

function apiError(status: number, code: string, message = code): unknown {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { ok: false, data: null, error: { code, message }, meta: null } },
  })
}

describe('useMeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('load で GET /me の結果を持つ', async () => {
    vi.mocked(baseApi.getMe).mockResolvedValue(makeMe())
    const store = useMeStore()

    await store.load()

    expect(store.me?.email).toBe('user@example.com')
    expect(store.attempted).toBe(true)
    expect(store.restricted).toBe(false)
    expect(store.loading).toBe(false)
  })

  it.each([
    ['停止中', makeMe({ status: 'suspended' }), true],
    ['admin の準備中', makeMe({ is_admin: true, admin_state: 'pending' }), true],
    ['admin の確認待ち', makeMe({ is_admin: true, admin_state: 'awaiting' }), true],
    ['admin で状態が無い', makeMe({ is_admin: true, admin_state: null }), true],
    ['有効な admin', makeMe({ is_admin: true, admin_state: 'active' }), false],
    ['有効なお客様', makeMe(), false],
  ])('%s は restricted=%s', async (_label, me, restricted) => {
    vi.mocked(baseApi.getMe).mockResolvedValue(me)
    const store = useMeStore()

    await store.load()

    expect(store.restricted).toBe(restricted)
  })

  // 状態が読めないときは /account だけを通す (fail-closed)
  it('読めなかったら restricted で、エラーのコードと文言を持つ', async () => {
    vi.mocked(baseApi.getMe).mockRejectedValue(
      apiError(403, 'USER_NOT_REGISTERED', 'この利用者は登録されていません'),
    )
    const store = useMeStore()

    await store.load()

    expect(store.me).toBeNull()
    expect(store.attempted).toBe(true)
    expect(store.restricted).toBe(true)
    expect(store.errorCode).toBe('USER_NOT_REGISTERED')
    expect(store.error).toBe('この利用者は登録されていません')
  })

  it('エンベロープの無い失敗は汎用の文言になる', async () => {
    vi.mocked(baseApi.getMe).mockRejectedValue(new Error('Network Error'))
    const store = useMeStore()

    await store.load()

    expect(store.errorCode).toBeNull()
    expect(store.error).toContain('アカウントの状態を読めませんでした')
  })

  it('noteDenied で API に断られた理由のコードを控え、load では消えず clear で消える', async () => {
    vi.mocked(baseApi.getMe).mockResolvedValue(makeMe())
    const store = useMeStore()

    store.noteDenied('ADMIN_SESSION_STALE')
    expect(store.denied).toBe('ADMIN_SESSION_STALE')

    await store.load()
    expect(store.denied).toBe('ADMIN_SESSION_STALE')

    store.clear()
    expect(store.denied).toBeNull()
    expect(store.me).toBeNull()
    expect(store.attempted).toBe(false)
  })

  it('registerTotp は POST /me/totp-registered を呼び、admin_state を確認待ちにする', async () => {
    vi.mocked(baseApi.getMe).mockResolvedValue(makeMe({ is_admin: true, admin_state: 'pending' }))
    vi.mocked(baseApi.registerTotp).mockResolvedValue({ admin_state: 'awaiting' })
    const store = useMeStore()
    await store.load()

    await store.registerTotp()

    expect(baseApi.registerTotp).toHaveBeenCalledTimes(1)
    expect(store.me?.admin_state).toBe('awaiting')
    expect(store.me?.mfa_registered).toBe(true)
  })

  it('registerTotp の失敗は API の文言を例外で返す', async () => {
    vi.mocked(baseApi.registerTotp).mockRejectedValue(
      apiError(503, 'TEMPORARILY_UNAVAILABLE', '一時的に処理できませんでした'),
    )
    const store = useMeStore()

    await expect(store.registerTotp()).rejects.toThrow('一時的に処理できませんでした')
  })
})
