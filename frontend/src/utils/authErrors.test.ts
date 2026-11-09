import { describe, expect, it } from 'vitest'
import { isSignInSessionError, toJaMessage } from './authErrors'

function namedError(name: string, message: string): Error {
  return Object.assign(new Error(message), { name })
}

const SESSION_EXPIRED = 'サインインの手続きの期限が切れました。もう一度最初からお試しください'
const PASSWORD_ERROR = 'メールアドレスまたはパスワードが正しくありません'
const SESSION_MESSAGES = [
  'Invalid session for the user, session is expired',
  'Invalid session for the user, session is expired.',
  'Invalid session for the user, session can only be used once',
  'Invalid session for the user, session can only be used once.',
  'Invalid session for the user',
  'Invalid session for the user.',
]

describe('サインインのセッションエラー', () => {
  it.each(SESSION_MESSAGES)('Cognito の NotAuthorizedException と既知の文言 %s を判定する', (message) => {
    const error = namedError('NotAuthorizedException', message)

    expect(isSignInSessionError(error)).toBe(true)
    expect(toJaMessage(error, { passwordlessSignIn: true })).toBe(SESSION_EXPIRED)
  })

  it.each(SESSION_MESSAGES)('パスワード認証では文言 %s でも従来の案内を保つ', (message) => {
    expect(toJaMessage(namedError('NotAuthorizedException', message))).toBe(PASSWORD_ERROR)
  })

  it.each([
    ['NotAuthorizedException', 'Incorrect username or password.'],
    ['NotAuthorizedException', 'Access Token has expired'],
    ['NotAuthorizedException', 'Refresh Token has expired'],
    ['NotAuthorizedException', 'Something went wrong with the session'],
    ['NotAuthorizedException', 'Invalid session for the user, an unknown problem occurred.'],
    ['CodeMismatchException', 'Invalid session for the user, session is expired.'],
    ['NetworkError', 'Invalid session for the user, session is expired.'],
    ['SignInException', 'Another sign in session error'],
    ['Error', 'Invalid session for the user, session is expired.'],
  ])('名前 %s と文言 %s が一致しないエラーを期限切れにしない', (name, message) => {
    const error = namedError(name, message)

    expect(isSignInSessionError(error)).toBe(false)
    expect(toJaMessage(error, { passwordlessSignIn: true })).not.toBe(SESSION_EXPIRED)
  })

  it.each([null, undefined, 'session is expired', { name: 'NotAuthorizedException', message: SESSION_MESSAGES[0] }])(
    'Error でない値 %j を判定しない',
    (error) => {
      expect(isSignInSessionError(error)).toBe(false)
      expect(toJaMessage(error, { passwordlessSignIn: true })).toBe('予期しないエラーが発生しました')
    },
  )
})

describe('toJaMessage', () => {
  it('コード不一致の既定の文言にはメールを含めない', () => {
    const message = toJaMessage(namedError('CodeMismatchException', 'Invalid code or auth state for the user.'))

    expect(message).toBe('確認コードが違います。もう一度お確かめください')
    expect(message).not.toContain('メール')
  })

  it('メールのコードを確かめるときだけいちばん新しいメールを案内する', () => {
    expect(toJaMessage(namedError('CodeMismatchException', 'Invalid code'), { emailCode: true })).toBe(
      '確認コードが違います。いちばん新しいメールのコードを入れてください',
    )
  })

  it('パスワードなしの入口でもメールのコードの段以外は中立な文言を保つ', () => {
    expect(toJaMessage(namedError('CodeMismatchException', 'Invalid code'), { passwordlessSignIn: true })).toBe(
      '確認コードが違います。もう一度お確かめください',
    )
  })

  it('コードそのものの期限切れには送り直しを案内する', () => {
    expect(toJaMessage(namedError('ExpiredCodeException', 'Invalid code provided, please request a code again.'))).toBe(
      '確認コードの有効期限が切れています。コードを送り直してください',
    )
  })

  it('通常の NotAuthorizedException はパスワードなしでも従来の案内を保つ', () => {
    expect(
      toJaMessage(namedError('NotAuthorizedException', 'Incorrect username or password.'), { passwordlessSignIn: true }),
    ).toBe(PASSWORD_ERROR)
  })
})
