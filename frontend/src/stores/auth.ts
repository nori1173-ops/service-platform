import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  confirmSignIn,
  fetchAuthSession,
  fetchMFAPreference,
  getCurrentUser,
  setUpTOTP,
  signIn,
  signOut,
  updateMFAPreference,
  verifyTOTPSetup,
  type SignInOutput,
} from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import { isPasskeySignInError, toJaMessage } from '@/utils/authErrors'
import { useDeviceStore } from './device'
import { useLandingStore } from './landing'
import { useMeStore } from './me'
import { usePasskeysStore } from './passkeys'

const TOTP_ISSUER = 'SERVICE-PLATFORM'

/** お客様と社員がサインインに使う要素 (ADR D1)。パスワードは admin のサインインだけで使う */
export type SignInFactor = 'WEB_AUTHN' | 'EMAIL_OTP'

export type Pending =
  | { kind: 'select-factor'; factors: string[] }
  | { kind: 'email-code'; destination?: string }
  | { kind: 'totp-code' }
  | { kind: 'new-password' }
  | null

export interface TotpSetup {
  setupUri: string
  sharedSecret: string
}

/** ID トークンの `cognito:groups` は配列でも `"[admin, ops]"` 形式の文字列でも来る */
function toGroups(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.filter((group): group is string => typeof group === 'string')
  }
  if (typeof raw === 'string') {
    return raw
      .replace(/^\[/, '')
      .replace(/\]$/, '')
      .split(/[\s,]+/)
      .filter((group) => group.length > 0)
  }
  return []
}

export const useAuthStore = defineStore('auth', () => {
  const email = ref<string | null>(null)
  const groups = ref<string[]>([])
  const initialized = ref(false)
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const pending = ref<Pending>(null)
  /** サインインを始めたメールアドレス。メールのコードの再送 (サインインのやり直し) に使う */
  const signInEmail = ref<string | null>(null)
  /** パスキーでのサインインが取り消し・非対応などで使えなかった */
  const passkeyFailed = ref(false)
  const availableFactors = ref<string[] | null>(null)
  /** 直前のサインインを済ませた要素。メールのコードで入ったらパスキーの登録を勧める */
  const lastFactor = ref<SignInFactor | 'PASSWORD' | null>(null)
  /** 認証セッションの世代。サインアウトとサインインの完了で進め、待機の前後で同じ利用者かを見分ける */
  const session = ref(0)
  let attempt: SignInFactor | 'PASSWORD' | null = null
  let signInRevision = 0

  const isAuthenticated = computed(() => email.value !== null)
  const isAdmin = computed(() => groups.value.includes('admin'))
  const passwordOnly = computed(() => {
    const current = pending.value
    return (
      current?.kind === 'select-factor' &&
      !current.factors.some((factor) => factor === 'WEB_AUTHN' || factor === 'EMAIL_OTP')
    )
  })

  function clear(): void {
    session.value += 1
    email.value = null
    groups.value = []
    pending.value = null
    availableFactors.value = null
    useMeStore().clear()
    usePasskeysStore().clear()
    useDeviceStore().reset()
    useLandingStore().clear()
  }

  async function checkAuth(): Promise<void> {
    try {
      const user = await getCurrentUser()
      const session = await fetchAuthSession()
      email.value = user.signInDetails?.loginId ?? user.username
      groups.value = toGroups(session.tokens?.idToken?.payload['cognito:groups'])
    } catch {
      clear()
    } finally {
      initialized.value = true
    }
  }

  async function route(result: SignInOutput): Promise<void> {
    const step = result.nextStep
    switch (step.signInStep) {
      case 'DONE':
        session.value += 1
        pending.value = null
        lastFactor.value = attempt
        useMeStore().clear()
        usePasskeysStore().clear()
        useDeviceStore().reset()
        useLandingStore().clear()
        await checkAuth()
        return
      case 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION':
        availableFactors.value = [...(step.availableChallenges ?? [])]
        pending.value = { kind: 'select-factor', factors: [...(step.availableChallenges ?? [])] }
        return
      case 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE':
        attempt = 'EMAIL_OTP'
        pending.value = { kind: 'email-code', destination: step.codeDeliveryDetails?.destination }
        return
      case 'CONFIRM_SIGN_IN_WITH_TOTP_CODE':
        pending.value = { kind: 'totp-code' }
        return
      case 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED':
        pending.value = { kind: 'new-password' }
        return
      case 'CONTINUE_SIGN_IN_WITH_MFA_SELECTION':
        await route(await confirmSignIn({ challengeResponse: 'TOTP' }))
        return
      default:
        throw new Error(`許可されていないサインインステップ: ${step.signInStep}`)
    }
  }

  async function run<T>(action: () => Promise<T>, options: { emailCode?: boolean } = {}): Promise<T> {
    const revision = signInRevision
    isLoading.value = true
    error.value = null
    try {
      return await action()
    } catch (e: unknown) {
      error.value = toJaMessage(e, options)
      passkeyFailed.value = isPasskeySignInError(e)
      if (passkeyFailed.value && availableFactors.value === null && signInEmail.value) {
        try {
          // 要素を選ばず候補だけを照会する。メールの送信は要求しない。
          const result = await signIn({ username: signInEmail.value, options: { authFlowType: 'USER_AUTH' } })
          if (revision === signInRevision && result.nextStep.signInStep === 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION') {
            availableFactors.value = [...(result.nextStep.availableChallenges ?? [])]
          }
        } catch {
          if (revision === signInRevision) availableFactors.value = null
        }
      }
      throw e
    } finally {
      isLoading.value = false
    }
  }

  /** メールアドレスでサインインを始める (USER_AUTH)。既定はパスキーを第一候補にする (ADR D1) */
  function startSignIn(username: string, preferred: SignInFactor = 'WEB_AUTHN'): Promise<void> {
    signInRevision += 1
    pending.value = null
    passkeyFailed.value = false
    availableFactors.value = null
    signInEmail.value = username
    attempt = preferred
    return run(async () => {
      const result = await signIn({
        username,
        options: { authFlowType: 'USER_AUTH', preferredChallenge: preferred },
      })
      await route(result)
    })
  }

  /** Cognito の候補 (SELECT_CHALLENGE) から要素を選ぶ */
  function selectFactor(factor: SignInFactor): Promise<void> {
    passkeyFailed.value = false
    attempt = factor
    return run(async () => {
      await route(await confirmSignIn({ challengeResponse: factor }))
    })
  }

  function submitEmailCode(code: string): Promise<void> {
    return run(async () => {
      await route(await confirmSignIn({ challengeResponse: code }))
    }, { emailCode: true })
  }

  /** Amplify 6.20 ではメールのコードの送り直しはサインインのやり直しになる */
  function resendEmailCode(): Promise<void> {
    const username = signInEmail.value
    if (!username) {
      return Promise.resolve()
    }
    return startSignIn(username, 'EMAIL_OTP')
  }

  /** admin のサインイン (パスワード + TOTP、ADR D9)。パスワードはストアに残さない */
  function startAdminSignIn(username: string, password: string): Promise<void> {
    signInRevision += 1
    pending.value = null
    passkeyFailed.value = false
    availableFactors.value = null
    signInEmail.value = username
    attempt = 'PASSWORD'
    return run(async () => {
      const result = await signIn({
        username,
        password,
        options: { authFlowType: 'USER_AUTH', preferredChallenge: 'PASSWORD_SRP' },
      })
      await route(result)
    })
  }

  function submitTotp(code: string): Promise<void> {
    return run(async () => {
      await route(await confirmSignIn({ challengeResponse: code }))
    })
  }

  function submitNewPassword(newPassword: string): Promise<void> {
    return run(async () => {
      await route(await confirmSignIn({ challengeResponse: newPassword }))
    })
  }

  /** 途中のサインインを捨てる。入口 (/login と /login/admin) を開くたびにも呼び、前の入口の段を持ち越さない */
  function cancelSignIn(): void {
    signInRevision += 1
    pending.value = null
    error.value = null
    passkeyFailed.value = false
    signInEmail.value = null
    availableFactors.value = null
    attempt = null
  }

  /** admin の TOTP の登録を始める。共有の秘密は呼び出し元の画面だけが持ち、ストアに残さない */
  function beginTotpSetup(): Promise<TotpSetup> {
    return run(async () => {
      const details = await setUpTOTP()
      return {
        setupUri: details.getSetupUri(TOTP_ISSUER, email.value ?? undefined).toString(),
        sharedSecret: details.sharedSecret,
      }
    })
  }

  /** 認証アプリのコードを確かめてから、TOTP を優先の MFA にする (ADR D9) */
  function confirmTotpSetup(code: string): Promise<void> {
    return run(async () => {
      await verifyTOTPSetup({ code })
      await updateMFAPreference({ totp: 'PREFERRED' })
    })
  }

  async function totpEnabled(): Promise<boolean> {
    const preference = await fetchMFAPreference()
    return preference.enabled?.includes('TOTP') ?? false
  }

  async function logout(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await signOut()
    } finally {
      clear()
      isLoading.value = false
    }
  }

  Hub.listen('auth', ({ payload }) => {
    if (payload.event === 'tokenRefresh_failure') {
      clear()
    }
  })

  return {
    email,
    groups,
    initialized,
    isLoading,
    error,
    pending,
    signInEmail,
    passkeyFailed,
    availableFactors,
    lastFactor,
    session,
    isAuthenticated,
    isAdmin,
    passwordOnly,
    checkAuth,
    startSignIn,
    selectFactor,
    submitEmailCode,
    resendEmailCode,
    startAdminSignIn,
    submitTotp,
    submitNewPassword,
    cancelSignIn,
    beginTotpSetup,
    confirmTotpSetup,
    totpEnabled,
    logout,
  }
})
