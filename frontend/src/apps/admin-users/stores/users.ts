import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { adminUsersApi } from '../api'
import type {
  ActivateInput,
  InviteInput,
  InviteResult,
  ProfileInput,
  ReinviteInput,
  ReinviteResult,
  RestoreInput,
  TemporaryPasswordResult,
  UserDetail,
  UserSummary,
} from '../types'

/** 操作の結果。失敗はサーバの code と文言 (日本語) をそのまま画面に渡す */
export type OpResult<T> = { ok: true; data: T } | { ok: false; code: string | null; message: string }

const OPERATION_FAILED = '操作できませんでした。状態を確かめてからやり直してください'

function failureOf(error: unknown): { ok: false; code: string | null; message: string } {
  const api = extractApiError(error)
  return { ok: false, code: api?.code ?? null, message: api?.message ?? OPERATION_FAILED }
}

/**
 * 一覧と詳細は毎回サーバから読み直す (台帳と Cognito の状態が一緒に変わるため)。
 * 一時パスワードは操作の結果で呼び出し元に返すだけで、ここには持たない。
 */
export const useUsersStore = defineStore('admin-users/users', () => {
  const users = ref<UserSummary[]>([])
  /** 呼び出し元自身の user_id。一覧を取るまでは空 */
  const selfUserId = ref('')
  const loading = ref(false)
  const error = ref<string | null>(null)
  const detail = ref<UserDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref<string | null>(null)
  let detailRequest = 0

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const response = await adminUsersApi.listUsers()
      users.value = response.users
      selfUserId.value = response.self_user_id
    } catch (e: unknown) {
      users.value = []
      error.value = extractApiError(e)?.message ?? '利用者の一覧を読めませんでした'
    } finally {
      loading.value = false
    }
  }

  /**
   * 最後に頼んだ利用者の応答だけを詳細にする (先に頼んだ応答が後から届いても上書きしない)。
   * 別の利用者を読み始めたら前の詳細は捨てる (前の利用者の詳細と操作を出さない)。
   */
  async function fetchDetail(userId: string): Promise<void> {
    const request = ++detailRequest
    if (detail.value?.user_id !== userId) {
      detail.value = null
    }
    detailLoading.value = true
    detailError.value = null
    try {
      const loaded = await adminUsersApi.getUser(userId)
      if (request === detailRequest) {
        detail.value = loaded
      }
    } catch (e: unknown) {
      if (request === detailRequest) {
        detail.value = null
        detailError.value = extractApiError(e)?.message ?? '利用者の詳細を読めませんでした'
      }
    } finally {
      if (request === detailRequest) {
        detailLoading.value = false
      }
    }
  }

  /** 操作の後は、成否を問わず詳細を読み直す (途中まで進んだ操作の状態を見せる) */
  async function operate<T>(userId: string, action: () => Promise<T>): Promise<OpResult<T>> {
    let result: OpResult<T>
    try {
      result = { ok: true, data: await action() }
    } catch (e: unknown) {
      result = failureOf(e)
    }
    await fetchDetail(userId)
    if (result.ok) {
      await fetch()
    }
    return result
  }

  async function invite(input: InviteInput): Promise<OpResult<InviteResult>> {
    try {
      const data = await adminUsersApi.invite(input)
      await fetch()
      return { ok: true, data }
    } catch (e: unknown) {
      return failureOf(e)
    }
  }

  return {
    users,
    selfUserId,
    loading,
    error,
    detail,
    detailLoading,
    detailError,
    fetch,
    fetchDetail,
    invite,
    updateProfile: (userId: string, input: ProfileInput) =>
      operate(userId, () => adminUsersApi.updateProfile(userId, input)),
    changeEmail: (userId: string, email: string) =>
      operate(userId, () => adminUsersApi.changeEmail(userId, email)),
    suspend: (userId: string, reason: string | null) =>
      operate(userId, () => adminUsersApi.suspend(userId, reason)),
    restore: (userId: string, input: RestoreInput) =>
      operate(userId, () => adminUsersApi.restore(userId, input)),
    reinvite: (userId: string, input: ReinviteInput): Promise<OpResult<ReinviteResult>> =>
      operate(userId, () => adminUsersApi.reinvite(userId, input)),
    activateAdmin: (userId: string, input: ActivateInput) =>
      operate(userId, () => adminUsersApi.activateAdmin(userId, input)),
    issueTemporaryPassword: (userId: string): Promise<OpResult<TemporaryPasswordResult>> =>
      operate(userId, () => adminUsersApi.issueTemporaryPassword(userId)),
    revokeAdmin: (userId: string) => operate(userId, () => adminUsersApi.revokeAdmin(userId)),
  }
})
