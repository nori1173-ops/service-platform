import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
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
import { useAuthStore } from './auth'
import { useDeviceStore } from './device'
import { useLandingStore } from './landing'
import { useMeStore } from './me'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const EMAIL = 'user@example.com'
const PASSWORD = 'Abcdefghij12'
const SETUP_URI = 'otpauth://totp/SERVICE-PLATFORM:user@example.com?secret=JBSWY3DPEHPK3PXP'
const SHARED_SECRET = 'JBSWY3DPEHPK3PXP'

function outputOf(nextStep: unknown, isSignedIn = false): SignInOutput {
  return { isSignedIn, nextStep } as unknown as SignInOutput
}

function namedError(name: string, message = name): Error {
  const error = new Error(message)
  error.name = name
  return error
}

function mockSession(groups?: unknown): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: 'sub-1234',
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  const payload: Record<string, unknown> = {}
  if (groups !== undefined) {
    payload['cognito:groups'] = groups
  }
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: { idToken: { payload } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
}

describe('useAuthStore: お客様のサインイン (USER_AUTH)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('startSignIn は USER_AUTH でパスキーを第一候補にする', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    const store = useAuthStore()

    await store.startSignIn(EMAIL)

    expect(signIn).toHaveBeenCalledWith({
      username: EMAIL,
      options: { authFlowType: 'USER_AUTH', preferredChallenge: 'WEB_AUTHN' },
    })
    expect(store.pending).toBeNull()
    expect(store.isAuthenticated).toBe(true)
    expect(store.lastFactor).toBe('WEB_AUTHN')
  })

  it('startSignIn に EMAIL_OTP を渡すとメールのコードを第一候補にする', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({
        signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE',
        codeDeliveryDetails: { deliveryMedium: 'EMAIL', destination: 'u***@e***' },
      }),
    )
    const store = useAuthStore()

    await store.startSignIn(EMAIL, 'EMAIL_OTP')

    expect(signIn).toHaveBeenCalledWith({
      username: EMAIL,
      options: { authFlowType: 'USER_AUTH', preferredChallenge: 'EMAIL_OTP' },
    })
    expect(store.pending).toEqual({ kind: 'email-code', destination: 'u***@e***' })
  })

  it('パスキーが無い利用者は候補の選択になり、候補の一覧が入る', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({
        signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION',
        availableChallenges: ['EMAIL_OTP', 'PASSWORD'],
      }),
    )
    const store = useAuthStore()

    await store.startSignIn(EMAIL)

    expect(store.pending).toEqual({ kind: 'select-factor', factors: ['EMAIL_OTP', 'PASSWORD'] })
    expect(store.passwordOnly).toBe(false)
  })

  // TOTP を持つ admin は候補がパスワードだけになる (PoC 5)。/login で行き止まりにしない
  it('候補にパスワードしか無いと passwordOnly になる', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({
        signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION',
        availableChallenges: ['PASSWORD', 'PASSWORD_SRP'],
      }),
    )
    const store = useAuthStore()

    await store.startSignIn(EMAIL)

    expect(store.pending).toEqual({
      kind: 'select-factor',
      factors: ['PASSWORD', 'PASSWORD_SRP'],
    })
    expect(store.passwordOnly).toBe(true)
  })

  it('selectFactor で選んだ要素を confirmSignIn に渡し、メールのコードの入力になる', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({
        signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION',
        availableChallenges: ['EMAIL_OTP'],
      }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }),
    )
    const store = useAuthStore()
    await store.startSignIn(EMAIL)

    await store.selectFactor('EMAIL_OTP')

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: 'EMAIL_OTP' })
    expect(store.pending).toEqual({ kind: 'email-code', destination: undefined })
  })

  it('submitEmailCode でコードを送り、DONE ならサインインが済む', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    const store = useAuthStore()
    await store.startSignIn(EMAIL, 'EMAIL_OTP')

    await store.submitEmailCode('12345678')

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: '12345678' })
    expect(store.pending).toBeNull()
    expect(store.email).toBe(EMAIL)
    expect(store.lastFactor).toBe('EMAIL_OTP')
  })

  // Amplify 6.20 のメールのコードの再送はサインインのやり直しになる (計画 4.3 のリスク)
  it('resendEmailCode は同じメールアドレスでメールのコードのサインインをやり直す', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }),
    )
    const store = useAuthStore()
    await store.startSignIn(EMAIL)
    vi.mocked(signIn).mockClear()

    await store.resendEmailCode()

    expect(signIn).toHaveBeenCalledWith({
      username: EMAIL,
      options: { authFlowType: 'USER_AUTH', preferredChallenge: 'EMAIL_OTP' },
    })
    expect(store.pending).toEqual({ kind: 'email-code', destination: undefined })
  })

  it('送り直しに失敗すると途中の状態を捨て、アドレスと失敗理由を残す', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }))
    const store = useAuthStore()
    await store.startSignIn(EMAIL, 'EMAIL_OTP')
    const error = namedError('CodeDeliveryFailureException')
    vi.mocked(signIn).mockRejectedValue(error)

    await expect(store.resendEmailCode()).rejects.toThrow(error)

    expect(store.pending).toBeNull()
    expect(store.signInEmail).toBe(EMAIL)
    expect(store.error).toContain('確認コードを送信できませんでした')
    expect(store.isLoading).toBe(false)
  })

  it('メールのコード以外の処理ではコード不一致を中立な文言で案内する', async () => {
    vi.mocked(signIn).mockRejectedValue(namedError('CodeMismatchException'))
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL, 'EMAIL_OTP')).rejects.toThrow()

    expect(store.error).toBe('確認コードが違います。もう一度お確かめください')
  })

  it('パスキーの確認を取り消すと日本語の文言になり、メールのコードへの切り替えを勧める', async () => {
    const error = namedError('PasskeyAuthenticationCanceled')
    vi.mocked(signIn).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL)).rejects.toThrow(error)

    expect(store.error).toContain('パスキーの確認が取り消されました')
    expect(store.passkeyFailed).toBe(true)
    expect(store.isLoading).toBe(false)
  })

  it.each([
    ['PasskeyNotSupported', 'パスキーに対応していません'],
    ['PasskeyAlreadyExists', '既にパスキーが登録されています'],
    ['PasskeyRegistrationCanceled', 'パスキーの登録が取り消されました'],
    ['RelyingPartyMismatch', 'このサイトのパスキーとして使えません'],
  ])('%s が日本語の文言になる', async (name, text) => {
    vi.mocked(signIn).mockRejectedValue(namedError(name))
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL)).rejects.toThrow()

    expect(store.error).toContain(text)
  })

  it('パスキー以外の失敗では passkeyFailed にならない', async () => {
    vi.mocked(signIn).mockRejectedValue(namedError('TooManyRequestsException'))
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL)).rejects.toThrow()

    expect(store.passkeyFailed).toBe(false)
    expect(store.error).toContain('アクセスが集中しています')
  })

  it('CodeMismatchException が日本語の文言になる', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }),
    )
    vi.mocked(confirmSignIn).mockRejectedValue(namedError('CodeMismatchException'))
    const store = useAuthStore()
    await store.startSignIn(EMAIL, 'EMAIL_OTP')

    await expect(store.submitEmailCode('00000000')).rejects.toThrow()

    expect(store.error).toBe('確認コードが違います。いちばん新しいメールのコードを入れてください')
  })

  it('許可されていない signInStep は例外', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'CONFIRM_SIGN_UP' }))
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL)).rejects.toThrow(
      '許可されていないサインインステップ: CONFIRM_SIGN_UP',
    )
    expect(store.isLoading).toBe(false)
  })

  it('cancelSignIn で途中の段と文言が消える', async () => {
    vi.mocked(signIn).mockRejectedValue(namedError('PasskeyAuthenticationCanceled'))
    const store = useAuthStore()
    await expect(store.startSignIn(EMAIL)).rejects.toThrow()

    store.cancelSignIn()

    expect(store.pending).toBeNull()
    expect(store.error).toBeNull()
    expect(store.passkeyFailed).toBe(false)
  })

  // 入口を切り替えたら、前の入口の途中状態 (宛先・選んだ要素) を持ち越さない (レビューの Important 1)
  it('cancelSignIn でサインインを始めたメールアドレスも消え、送り直しは何もしない', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_EMAIL_CODE' }),
    )
    const store = useAuthStore()
    await store.startSignIn(EMAIL, 'EMAIL_OTP')
    vi.mocked(signIn).mockClear()

    store.cancelSignIn()
    await store.resendEmailCode()

    expect(store.signInEmail).toBeNull()
    expect(signIn).not.toHaveBeenCalled()
  })

  it('辞書に無い例外は汎用文言に例外名を添える', async () => {
    vi.mocked(signIn).mockRejectedValue(namedError('UnknownCognitoException', 'x'))
    const store = useAuthStore()

    await expect(store.startSignIn(EMAIL)).rejects.toThrow()

    expect(store.error).toBe('予期しないエラーが発生しました（UnknownCognitoException）')
  })

  // 招待制なのでサインアップとパスワードの再設定の関数を持たない (ADR D2 / D1)
  it('サインアップとパスワードの再設定の関数を持たない', () => {
    const store = useAuthStore() as unknown as Record<string, unknown>

    for (const name of [
      'login',
      'register',
      'confirmRegistration',
      'resendCode',
      'requestPasswordReset',
      'submitPasswordReset',
    ]) {
      expect(store[name], name).toBeUndefined()
    }
  })
})

describe('useAuthStore: admin のサインイン (パスワード + TOTP)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('startAdminSignIn は USER_AUTH の PASSWORD_SRP でパスワードを渡す', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    const store = useAuthStore()

    await store.startAdminSignIn(EMAIL, PASSWORD)

    expect(signIn).toHaveBeenCalledWith({
      username: EMAIL,
      password: PASSWORD,
      options: { authFlowType: 'USER_AUTH', preferredChallenge: 'PASSWORD_SRP' },
    })
    expect(store.pending).toEqual({ kind: 'totp-code' })
  })

  it('submitTotp で TOTP のコードを送り、DONE でサインインが済む', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession(['admin'])
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, PASSWORD)

    await store.submitTotp('123456')

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: '123456' })
    expect(store.isAdmin).toBe(true)
    expect(store.lastFactor).toBe('PASSWORD')
  })

  it('submitTotp の CodeMismatchException の文言にはメールを含めない', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }))
    vi.mocked(confirmSignIn).mockRejectedValue(namedError('CodeMismatchException'))
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, PASSWORD)

    await expect(store.submitTotp('000000')).rejects.toThrow()

    expect(store.error).toBe('確認コードが違います。もう一度お確かめください')
    expect(store.error).not.toContain('メール')
    expect(store.pending?.kind).toBe('totp-code')
  })

  it('一時パスワードのときは新しいパスワードの入力になり、submitNewPassword で決める', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession(['admin'])
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, 'Temporary123')
    expect(store.pending).toEqual({ kind: 'new-password' })

    await store.submitNewPassword(PASSWORD)

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: PASSWORD })
    expect(store.pending).toBeNull()
    expect(store.isAuthenticated).toBe(true)
  })

  // TOTP を持ったままの admin に一時パスワードを出した後の流れ (計画 8 章のリスク 5)
  it('本パスワードを決めた後に TOTP を求められても続けられる', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED' }),
    )
    vi.mocked(confirmSignIn)
      .mockResolvedValueOnce(outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }))
      .mockResolvedValueOnce(outputOf({ signInStep: 'DONE' }, true))
    mockSession(['admin'])
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, 'Temporary123')

    await store.submitNewPassword(PASSWORD)
    expect(store.pending).toEqual({ kind: 'totp-code' })

    await store.submitTotp('654321')
    expect(store.pending).toBeNull()
    expect(store.isAuthenticated).toBe(true)
  })

  it('CONTINUE_SIGN_IN_WITH_MFA_SELECTION では TOTP を選んで続ける', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONTINUE_SIGN_IN_WITH_MFA_SELECTION' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    const store = useAuthStore()

    await store.startAdminSignIn(EMAIL, PASSWORD)

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: 'TOTP' })
    expect(store.pending).toEqual({ kind: 'totp-code' })
  })

  it.each([
    'Incorrect username or password.',
    'Invalid session for the user, session is expired.',
    'Invalid session for the user, session can only be used once.',
    'Invalid session for the user.',
  ])('管理者の NotAuthorizedException (%s) は従来の文言を保つ', async (message) => {
    vi.mocked(signIn).mockRejectedValue(namedError('NotAuthorizedException', message))
    const store = useAuthStore()

    await expect(store.startAdminSignIn(EMAIL, 'wrong')).rejects.toThrow()

    expect(store.error).toBe('メールアドレスまたはパスワードが正しくありません')
  })

  it('InvalidPasswordException は 12 文字以上の方針の文言になる', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED' }),
    )
    vi.mocked(confirmSignIn).mockRejectedValue(namedError('InvalidPasswordException'))
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, 'Temporary123')

    await expect(store.submitNewPassword('short')).rejects.toThrow()

    expect(store.error).toContain('12 文字以上')
  })

  // パスワードはストアに残さない (画面のメモリだけで使う)
  it('パスワードをストアに残さない', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    const store = useAuthStore()
    await store.startAdminSignIn(EMAIL, 'Temporary123')
    await store.submitNewPassword(PASSWORD)

    const state = JSON.stringify(store.$state)
    expect(state).not.toContain('Temporary123')
    expect(state).not.toContain(PASSWORD)
  })
})

describe('useAuthStore: admin の TOTP の登録', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('beginTotpSetup は setUpTOTP の結果を返し、ストアに秘密を残さない', async () => {
    mockSession(['admin'])
    const getSetupUri = vi.fn(() => new URL(SETUP_URI))
    vi.mocked(setUpTOTP).mockResolvedValue({
      sharedSecret: SHARED_SECRET,
      getSetupUri,
    } as unknown as Awaited<ReturnType<typeof setUpTOTP>>)
    const store = useAuthStore()
    await store.checkAuth()

    const setup = await store.beginTotpSetup()

    expect(getSetupUri).toHaveBeenCalledWith('SERVICE-PLATFORM', EMAIL)
    expect(setup).toEqual({ setupUri: SETUP_URI, sharedSecret: SHARED_SECRET })
    expect(JSON.stringify(store.$state)).not.toContain(SHARED_SECRET)
  })

  it('confirmTotpSetup はコードを確かめてから TOTP を優先にする', async () => {
    const store = useAuthStore()

    await store.confirmTotpSetup('123456')

    expect(verifyTOTPSetup).toHaveBeenCalledWith({ code: '123456' })
    expect(updateMFAPreference).toHaveBeenCalledWith({ totp: 'PREFERRED' })
    expect(vi.mocked(verifyTOTPSetup).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(updateMFAPreference).mock.invocationCallOrder[0],
    )
  })

  it('confirmTotpSetup のコードの誤りは日本語の文言になり、優先にしない', async () => {
    vi.mocked(verifyTOTPSetup).mockRejectedValue(namedError('EnableSoftwareTokenMFAException'))
    const store = useAuthStore()

    await expect(store.confirmTotpSetup('000000')).rejects.toThrow()

    expect(updateMFAPreference).not.toHaveBeenCalled()
    expect(store.error).toContain('認証コードが正しくありません')
  })

  it('confirmTotpSetup の CodeMismatchException の文言にはメールを含めない', async () => {
    vi.mocked(verifyTOTPSetup).mockRejectedValue(namedError('CodeMismatchException'))
    const store = useAuthStore()

    await expect(store.confirmTotpSetup('000000')).rejects.toThrow()

    expect(store.error).toBe('確認コードが違います。もう一度お確かめください')
    expect(store.error).not.toContain('メール')
    expect(updateMFAPreference).not.toHaveBeenCalled()
  })

  it('totpEnabled は Cognito の MFA の設定に TOTP があるかを返す', async () => {
    vi.mocked(fetchMFAPreference).mockResolvedValue({ enabled: ['TOTP'], preferred: 'TOTP' })
    const store = useAuthStore()
    expect(await store.totpEnabled()).toBe(true)

    vi.mocked(fetchMFAPreference).mockResolvedValue({})
    expect(await store.totpEnabled()).toBe(false)
  })
})

describe('useAuthStore: セッション', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('checkAuth が cognito:groups（配列）から groups を取る', async () => {
    mockSession(['admin', 'ops'])
    const store = useAuthStore()

    await store.checkAuth()

    expect(store.groups).toEqual(['admin', 'ops'])
    expect(store.email).toBe(EMAIL)
    expect(store.initialized).toBe(true)
  })

  it('checkAuth が cognito:groups（文字列 "[admin]"）でも配列にする', async () => {
    mockSession('[admin]')
    const store = useAuthStore()

    await store.checkAuth()

    expect(store.groups).toEqual(['admin'])
  })

  it('checkAuth 失敗で isAuthenticated が false かつ initialized が true', async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('not signed in'))
    const store = useAuthStore()
    store.email = EMAIL
    store.groups = ['admin']

    await store.checkAuth()

    expect(store.isAuthenticated).toBe(false)
    expect(store.groups).toEqual([])
    expect(store.initialized).toBe(true)
  })

  it('サインインが済むと前の利用者の me を捨てる', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    const me = useMeStore()
    const clear = vi.spyOn(me, 'clear')
    const store = useAuthStore()

    await store.startSignIn(EMAIL)

    expect(clear).toHaveBeenCalled()
  })

  it('logout は signOut を呼び、ストアと me を消す', async () => {
    mockSession(['admin'])
    const me = useMeStore()
    const clear = vi.spyOn(me, 'clear')
    const store = useAuthStore()
    await store.checkAuth()

    await store.logout()

    expect(signOut).toHaveBeenCalledTimes(1)
    expect(store.isAuthenticated).toBe(false)
    expect(clear).toHaveBeenCalled()
  })

  // 同じブラウザで別の利用者が入っても、前の利用者の通信機の選択と入力を見せない
  it('logout とサインインの完了で通信機ログインのストアを初期化する', async () => {
    mockSession([])
    const device = useDeviceStore()
    const reset = vi.spyOn(device, 'reset')
    const store = useAuthStore()
    await store.checkAuth()

    await store.logout()
    expect(reset).toHaveBeenCalledTimes(1)

    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    await store.startSignIn(EMAIL)
    expect(reset).toHaveBeenCalledTimes(2)
  })

  // : 前の利用者の近道の印を、次の利用者の通信機ログインの画面に持ち越さない
  it('logout とサインインの完了で近道の印を消す', async () => {
    mockSession([])
    const landing = useLandingStore()
    const store = useAuthStore()
    await store.checkAuth()
    landing.enter('12345678')

    await store.logout()
    expect(landing.account).toBeNull()

    landing.enter('12345678')
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    await store.startSignIn(EMAIL)
    expect(landing.account).toBeNull()
  })

  // : 待機の前後で同じ認証セッションかを、近道の判定が見分けられるようにする
  it('サインアウト・サインインの完了・セッションの消失で認証の世代が進む', async () => {
    mockSession([])
    const store = useAuthStore()
    await store.checkAuth()
    const initial = store.session

    await store.logout()
    expect(store.session).toBeGreaterThan(initial)

    const afterLogout = store.session
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    await store.startSignIn(EMAIL)
    expect(store.session).toBeGreaterThan(afterLogout)

    const afterSignIn = store.session
    const [, listener] = vi.mocked(Hub.listen).mock.calls[0]
    listener({ payload: { event: 'tokenRefresh_failure' } } as never)
    expect(store.session).toBeGreaterThan(afterSignIn)
  })

  it('tokenRefresh_failure でストアがクリアされる', async () => {
    mockSession(['admin'])
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.isAuthenticated).toBe(true)

    const [channel, listener] = vi.mocked(Hub.listen).mock.calls[0]
    expect(channel).toBe('auth')
    listener({ payload: { event: 'tokenRefresh_failure' } } as never)

    expect(store.isAuthenticated).toBe(false)
    expect(store.groups).toEqual([])
    expect(store.pending).toBeNull()
  })
})


describe('パスキーの代替案内', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

it.each([[['WEB_AUTHN', 'PASSWORD']], [['WEB_AUTHN', 'EMAIL_OTP']]])('パスキーの取消後は要素を選ばず候補を照会する: %s', async (factors) => {
  const error = namedError('PasskeyAuthenticationCanceled')
  vi.mocked(signIn).mockRejectedValueOnce(error).mockResolvedValueOnce({
    isSignedIn: false,
    nextStep: { signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION', availableChallenges: factors as never },
  })
  const store = useAuthStore()
  await expect(store.startSignIn(EMAIL)).rejects.toThrow(error)
  expect(signIn).toHaveBeenLastCalledWith({ username: EMAIL, options: { authFlowType: 'USER_AUTH' } })
  expect(store.availableFactors).toEqual(factors)
  expect(store.pending).toBeNull()
  expect(store.error).not.toContain('メールのコード')
})

it('選択後のパスキー取消では既知の候補を保持する', async () => {
  vi.mocked(signIn).mockResolvedValueOnce({ isSignedIn: false, nextStep: {
    signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION', availableChallenges: ['WEB_AUTHN', 'PASSWORD'],
  } })
  const store = useAuthStore()
  await store.startSignIn(EMAIL)
  vi.mocked(confirmSignIn).mockRejectedValueOnce(namedError('PasskeyAuthenticationCanceled'))
  await expect(store.selectFactor('WEB_AUTHN')).rejects.toThrow()
  expect(store.availableFactors).toEqual(['WEB_AUTHN', 'PASSWORD'])
  expect(signIn).toHaveBeenCalledTimes(1)
  store.cancelSignIn()
  expect(store.availableFactors).toBeNull()
})


it('候補の照会中にやり直したら、遅い応答で候補を戻さない', async () => {
  let resolve!: (value: SignInOutput) => void
  vi.mocked(signIn).mockRejectedValueOnce(namedError('PasskeyAuthenticationCanceled'))
    .mockReturnValueOnce(new Promise((done) => { resolve = done }))
  const store = useAuthStore()
  const attempt = store.startSignIn(EMAIL)
  const rejected = expect(attempt).rejects.toThrow()
  await vi.waitFor(() => expect(signIn).toHaveBeenCalledTimes(2))
  store.cancelSignIn()
  resolve({ isSignedIn: false, nextStep: {
    signInStep: 'CONTINUE_SIGN_IN_WITH_FIRST_FACTOR_SELECTION', availableChallenges: ['EMAIL_OTP'],
  } })
  await rejected
  expect(store.availableFactors).toBeNull()
  expect(store.error).toBeNull()
})

})
