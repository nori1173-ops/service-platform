import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { adminUsersApi } from '../api'
import { useUsersStore } from '../stores/users'
import type { UserDetail, UserSummary } from '../types'

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

const SELF_ID = '01JBEXAMPLE0000000000000001'
const USER_ID = '01JBEXAMPLE0000000000000002'
const TEMPORARY_PASSWORD = 'TempPassw0rdXyz12345'

function makeSummary(overrides: Partial<UserSummary> = {}): UserSummary {
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
    ...overrides,
  }
}

function makeDetail(overrides: Partial<UserDetail> = {}): UserDetail {
  return {
    ...makeSummary(),
    contact: null,
    note: null,
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

function apiError(status: number, code: string, message: string): unknown {
  return {
    response: { status, data: { ok: false, data: null, error: { code, message }, meta: null } },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(adminUsersApi.listUsers).mockResolvedValue({
    users: [makeSummary()],
    self_user_id: SELF_ID,
  })
  vi.mocked(adminUsersApi.getUser).mockResolvedValue(makeDetail())
})

describe('useUsersStore: 一覧と詳細', () => {
  it('fetch が一覧と自分の user_id を持つ', async () => {
    const store = useUsersStore()

    await store.fetch()

    expect(store.users).toHaveLength(1)
    expect(store.selfUserId).toBe(SELF_ID)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('fetch の失敗は API の文言になり一覧は空になる', async () => {
    vi.mocked(adminUsersApi.listUsers).mockRejectedValue(
      apiError(403, 'ADMIN_NOT_ACTIVE', '管理者として有効ではありません'),
    )
    const store = useUsersStore()

    await store.fetch()

    expect(store.users).toEqual([])
    expect(store.error).toBe('管理者として有効ではありません')
  })

  it('エンベロープの無い失敗は汎用の文言になる', async () => {
    vi.mocked(adminUsersApi.listUsers).mockRejectedValue(new Error('Network Error'))
    const store = useUsersStore()

    await store.fetch()

    expect(store.error).toBe('利用者の一覧を読めませんでした')
  })

  it('fetchDetail が詳細を持つ', async () => {
    const store = useUsersStore()

    await store.fetchDetail(USER_ID)

    expect(adminUsersApi.getUser).toHaveBeenCalledWith(USER_ID)
    expect(store.detail?.user_id).toBe(USER_ID)
    expect(store.detailError).toBeNull()
  })

  // 利用者を続けて開くと、先に頼んだ応答が後から届くことがある (レビューの Critical の素地)
  it('先に頼んだ詳細の応答が後から届いても、最後に頼んだ利用者の詳細を残す', async () => {
    let resolveFirst: (detail: UserDetail) => void = () => {}
    vi.mocked(adminUsersApi.getUser)
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce(makeDetail({ user_id: 'second', email: 'second@example.com' }))
    const store = useUsersStore()

    const first = store.fetchDetail('first')
    await store.fetchDetail('second')
    expect(store.detail?.user_id).toBe('second')
    expect(store.detailLoading).toBe(false)

    resolveFirst(makeDetail({ user_id: 'first', email: 'first@example.com' }))
    await first

    expect(store.detail?.user_id).toBe('second')
    expect(store.detailLoading).toBe(false)
  })

  it('別の利用者を読み始めたら前の詳細を捨てる', async () => {
    const store = useUsersStore()
    await store.fetchDetail(USER_ID)
    vi.mocked(adminUsersApi.getUser).mockImplementation(() => new Promise(() => {}))

    void store.fetchDetail('next')

    expect(store.detail).toBeNull()
    expect(store.detailLoading).toBe(true)
  })

  it('同じ利用者を読み直す間は詳細を残す', async () => {
    const store = useUsersStore()
    await store.fetchDetail(USER_ID)
    vi.mocked(adminUsersApi.getUser).mockImplementation(() => new Promise(() => {}))

    void store.fetchDetail(USER_ID)

    expect(store.detail?.user_id).toBe(USER_ID)
    expect(store.detailLoading).toBe(true)
  })

  it('fetchDetail の失敗は文言になり詳細を捨てる', async () => {
    vi.mocked(adminUsersApi.getUser).mockRejectedValue(
      apiError(404, 'USER_NOT_FOUND', '利用者が見つかりません。一覧を更新してください'),
    )
    const store = useUsersStore()

    await store.fetchDetail(USER_ID)

    expect(store.detail).toBeNull()
    expect(store.detailError).toBe('利用者が見つかりません。一覧を更新してください')
  })
})

describe('useUsersStore: 操作', () => {
  it('invite は結果を返して一覧を読み直す', async () => {
    vi.mocked(adminUsersApi.invite).mockResolvedValue({
      user_id: USER_ID,
      email: 'new@example.com',
      kind: 'customer',
      role: 'user',
      status: 'active',
      admin_state: null,
    })
    const store = useUsersStore()

    const result = await store.invite({ email: 'new@example.com', kind: 'customer', role: 'user' })

    expect(result).toMatchObject({ ok: true, data: { user_id: USER_ID } })
    expect(adminUsersApi.listUsers).toHaveBeenCalledTimes(1)
  })

  it('invite の失敗は API の文言とコードを返す', async () => {
    vi.mocked(adminUsersApi.invite).mockRejectedValue(
      apiError(409, 'USER_EXISTS', '同じメールアドレスの利用者がいます'),
    )
    const store = useUsersStore()

    const result = await store.invite({ email: 'dup@example.com', kind: 'customer', role: 'user' })

    expect(result).toEqual({
      ok: false,
      code: 'USER_EXISTS',
      message: '同じメールアドレスの利用者がいます',
    })
    expect(adminUsersApi.listUsers).not.toHaveBeenCalled()
  })

  // 一時パスワードは画面で 1 回だけ出す。ストアには残さない (計画 8 章のリスク 10)
  it.each([
    [
      'admin の招待',
      () => {
        vi.mocked(adminUsersApi.invite).mockResolvedValue({
          user_id: USER_ID,
          email: 'admin@example.com',
          kind: 'employee',
          role: 'admin',
          status: 'active',
          admin_state: 'pending',
          temporary_password: TEMPORARY_PASSWORD,
        })
        return useUsersStore().invite({ email: 'admin@example.com', kind: 'employee', role: 'admin' })
      },
    ],
    [
      'admin の再招待',
      () => {
        vi.mocked(adminUsersApi.reinvite).mockResolvedValue({
          user_id: USER_ID,
          email: 'admin@example.com',
          status: 'suspended',
          temporary_password: TEMPORARY_PASSWORD,
        })
        return useUsersStore().reinvite(USER_ID, { reason: 'TOTP の紛失' })
      },
    ],
    [
      '一時パスワードの発行',
      () => {
        vi.mocked(adminUsersApi.issueTemporaryPassword).mockResolvedValue({
          user_id: USER_ID,
          temporary_password: TEMPORARY_PASSWORD,
        })
        return useUsersStore().issueTemporaryPassword(USER_ID)
      },
    ],
  ])('%s の一時パスワードは結果で返し、ストアに残さない', async (_label, call) => {
    const store = useUsersStore()
    await store.fetchDetail(USER_ID)

    const result = await call()

    expect(result.ok && (result.data as { temporary_password?: string }).temporary_password).toBe(
      TEMPORARY_PASSWORD,
    )
    expect(JSON.stringify(store.$state)).not.toContain(TEMPORARY_PASSWORD)
  })

  it.each([
    ['suspend', () => useUsersStore().suspend(USER_ID, '盗難'), () => adminUsersApi.suspend, [USER_ID, '盗難']],
    [
      'restore',
      () => useUsersStore().restore(USER_ID, { verified_method: 'phone' }),
      () => adminUsersApi.restore,
      [USER_ID, { verified_method: 'phone' }],
    ],
    [
      'activateAdmin',
      () => useUsersStore().activateAdmin(USER_ID, { method: 'video', note: 'ビデオ通話' }),
      () => adminUsersApi.activateAdmin,
      [USER_ID, { method: 'video', note: 'ビデオ通話' }],
    ],
    ['revokeAdmin', () => useUsersStore().revokeAdmin(USER_ID), () => adminUsersApi.revokeAdmin, [USER_ID]],
    [
      'changeEmail',
      () => useUsersStore().changeEmail(USER_ID, 'b@example.com'),
      () => adminUsersApi.changeEmail,
      [USER_ID, 'b@example.com'],
    ],
    [
      'updateProfile',
      () => useUsersStore().updateProfile(USER_ID, { contact: '' }),
      () => adminUsersApi.updateProfile,
      [USER_ID, { contact: '' }],
    ],
  ])('%s は API を呼び、詳細と一覧を読み直す', async (_name, call, endpoint, args) => {
    vi.mocked(endpoint()).mockResolvedValue({} as never)
    const store = useUsersStore()

    const result = await call()

    expect(result.ok).toBe(true)
    expect(endpoint()).toHaveBeenCalledWith(...args)
    expect(adminUsersApi.getUser).toHaveBeenCalledWith(USER_ID)
    expect(adminUsersApi.listUsers).toHaveBeenCalled()
    expect(store.detail?.user_id).toBe(USER_ID)
  })

  it('操作の失敗は API の文言を返し、詳細だけ読み直す', async () => {
    vi.mocked(adminUsersApi.suspend).mockRejectedValue(
      apiError(409, 'LAST_ADMIN', '最後の有効な管理者には操作できません'),
    )
    const store = useUsersStore()

    const result = await store.suspend(USER_ID, null)

    expect(result).toEqual({
      ok: false,
      code: 'LAST_ADMIN',
      message: '最後の有効な管理者には操作できません',
    })
    expect(adminUsersApi.getUser).toHaveBeenCalledWith(USER_ID)
  })

  it('エンベロープの無い操作の失敗は汎用の文言を返す', async () => {
    vi.mocked(adminUsersApi.revokeAdmin).mockRejectedValue(new Error('timeout'))
    const store = useUsersStore()

    const result = await store.revokeAdmin(USER_ID)

    expect(result).toMatchObject({ ok: false, code: null })
    expect(result.ok || result.message).toContain('できませんでした')
  })
})
