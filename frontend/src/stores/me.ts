import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { baseApi } from '@/services/baseApi'
import { extractApiError } from '@/services/apiError'

export type UserStatus = 'active' | 'suspended'
export type AdminState = 'pending' | 'awaiting' | 'active'

/** 自分の状態 (`GET /api/base/me`、ADR D9 の例外 2)。ほかの利用者のことは含まない */
export interface Me {
  user_id: string
  email: string
  status: UserStatus
  restore_requires: 'reinvite' | null
  kind: 'customer' | 'employee' | null
  org_name: string | null
  /** Cognito の admin グループ (ID トークンのクレーム) */
  is_admin: boolean
  admin_state: AdminState | null
  /** 台帳に TOTP の登録の時刻があるか (値は返らない) */
  mfa_registered: boolean
}

const LOAD_FAILED = 'アカウントの状態を読めませんでした。しばらくしてから読み直してください'
const REGISTER_FAILED = 'TOTP の登録を知らせられませんでした。もう一度お試しください'

export const useMeStore = defineStore('me', () => {
  const me = ref<Me | null>(null)
  /** 読みに行ったか (成否を問わない)。ガードが 1 回だけ読むために使う */
  const attempted = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const errorCode = ref<string | null>(null)
  /** API に断られた理由 (403 の code)。アカウントの画面が案内に使う */
  const denied = ref<string | null>(null)
  let inflight: Promise<void> | null = null

  /** 停止中、admin の準備中・確認待ち、状態が読めない、のときはアカウントの画面だけを使う */
  const restricted = computed(() => {
    const current = me.value
    if (!current || current.status !== 'active') {
      return true
    }
    return current.is_admin && current.admin_state !== 'active'
  })

  async function fetchMe(): Promise<void> {
    loading.value = true
    error.value = null
    errorCode.value = null
    try {
      me.value = await baseApi.getMe()
    } catch (e: unknown) {
      me.value = null
      const api = extractApiError(e)
      errorCode.value = api?.code ?? null
      error.value = api?.message ?? LOAD_FAILED
    } finally {
      attempted.value = true
      loading.value = false
    }
  }

  function load(): Promise<void> {
    inflight ??= fetchMe().finally(() => {
      inflight = null
    })
    return inflight
  }

  function noteDenied(code: string): void {
    denied.value = code
  }

  function clear(): void {
    me.value = null
    attempted.value = false
    error.value = null
    errorCode.value = null
    denied.value = null
  }

  /** admin の TOTP の登録を終えた (ADR D9 の例外 1)。サーバはそれまでのセッションを失効させる */
  async function registerTotp(): Promise<void> {
    try {
      const result = await baseApi.registerTotp()
      if (me.value) {
        me.value = { ...me.value, admin_state: result.admin_state, mfa_registered: true }
      }
    } catch (e: unknown) {
      throw new Error(extractApiError(e)?.message ?? REGISTER_FAILED, { cause: e })
    }
  }

  return {
    me,
    attempted,
    loading,
    error,
    errorCode,
    denied,
    restricted,
    load,
    noteDenied,
    clear,
    registerTotp,
  }
})
