import axios, { type AxiosInstance } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { extractApiError } from './apiError'

export { extractApiError }

const REQUEST_TIMEOUT_MS = 30000

/**
 * 利用者の状態か admin の守りで断られた 403 (計画 2.6 の共通のエラーコード)。
 * サインアウトはせず、アカウントの画面で理由を見せる。
 */
const DENIED_CODES: ReadonlySet<string> = new Set([
  'USER_SUSPENDED',
  'USER_NOT_REGISTERED',
  'ADMIN_NOT_ACTIVE',
  'ADMIN_SESSION_STALE',
  'ADMIN_MFA_MISSING',
])

export interface AppApiOptions {
  /** 403 の停止・admin の守りでアカウントの画面へ移るか (既定 true)。base は画面が自分で扱う */
  redirectOnDenied?: boolean
}

function statusOf(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    return (error as { response?: { status?: number } }).response?.status
  }
  return undefined
}

/** ストアの `logout()`（`signOut()` + `clear()`）。失敗してもリダイレクトは止めない。 */
async function logoutQuietly(): Promise<void> {
  try {
    const { useAuthStore } = await import('@/stores/auth')
    await useAuthStore().logout()
  } catch {
    return
  }
}

async function redirectToLoginQuietly(): Promise<void> {
  try {
    const { default: router } = await import('@/router')
    const current = router.currentRoute.value
    if (current.name !== 'login') {
      await router.push({ name: 'login', query: { redirect: current.fullPath } })
    }
  } catch {
    return
  }
}

/**
 * 401 の後始末。元の 401 を呼び出し元へ返すため、ここでの失敗は握りつぶす。
 * ストアを未認証化してからでないと、ガードの「認証済み × login」分岐で
 * ホームへ跳ね返されてログイン画面に到達できない。
 */
async function handleUnauthorized(): Promise<void> {
  await logoutQuietly()
  await redirectToLoginQuietly()
}

/** 403 の停止・admin の守りの後始末。理由を控えて状態を読み直し、アカウントの画面へ移る */
async function handleDenied(code: string): Promise<void> {
  try {
    const { useMeStore } = await import('@/stores/me')
    const me = useMeStore()
    me.noteDenied(code)
    await me.load()
    const { default: router } = await import('@/router')
    if (router.currentRoute.value.name !== 'account') {
      await router.push({ name: 'account' })
    }
  } catch {
    return
  }
}

/** アプリ専用の axios インスタンスを作る（`/api/{appId}`） */
export function createAppApi(appId: string, options: AppApiOptions = {}): AxiosInstance {
  const redirectOnDenied = options.redirectOnDenied ?? true
  const instance = axios.create({
    baseURL: `/api/${appId}`,
    timeout: REQUEST_TIMEOUT_MS,
    headers: { 'Content-Type': 'application/json' },
  })

  instance.interceptors.request.use(async (config) => {
    const session = await fetchAuthSession()
    const idToken = session.tokens?.idToken?.toString()
    if (idToken) {
      config.headers.Authorization = `Bearer ${idToken}`
    }
    return config
  })

  instance.interceptors.response.use(
    (response) => response,
    async (error: unknown) => {
      const status = statusOf(error)
      if (status === 401) {
        await handleUnauthorized()
      } else if (status === 403 && redirectOnDenied) {
        const code = extractApiError(error)?.code
        if (code && DENIED_CODES.has(code)) {
          await handleDenied(code)
        }
      }
      return Promise.reject(error)
    },
  )

  return instance
}
