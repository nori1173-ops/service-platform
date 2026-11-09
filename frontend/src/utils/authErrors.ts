import { PASSWORD_POLICY_MESSAGE } from '@/utils/password'

const GENERIC_ERROR = '予期しないエラーが発生しました'

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  NotAuthorizedException: 'メールアドレスまたはパスワードが正しくありません',
  UserNotFoundException: 'メールアドレスまたはパスワードが正しくありません',
  PasswordResetRequiredException:
    'パスワードの再設定が必要です。別の管理者に一時パスワードの発行を依頼してください',
  TooManyRequestsException: 'アクセスが集中しています。しばらく時間をおいてから再試行してください',
  TooManyFailedAttemptsException:
    '失敗が続いたため一時的に制限されています。しばらく時間をおいてから再試行してください',
  LimitExceededException: '試行回数の上限に達しました。しばらく時間をおいてから再試行してください',
  CodeMismatchException: '確認コードが違います。もう一度お確かめください',
  ExpiredCodeException: '確認コードの有効期限が切れています。コードを送り直してください',
  CodeDeliveryFailureException: '確認コードを送信できませんでした。しばらくしてからやり直してください',
  EnableSoftwareTokenMFAException: '認証コードが正しくありません。認証アプリの表示を確認してください',
  SoftwareTokenMFANotFoundException: '認証アプリが登録されていません。別の管理者へ連絡してください',
  InvalidPasswordException: PASSWORD_POLICY_MESSAGE,
  InvalidParameterException: '入力内容が正しくありません',
  UserAlreadyAuthenticatedException:
    '既にサインインしています。サインアウトしてからやり直してください',
  NetworkError: 'ネットワークに接続できませんでした。通信環境を確認してください',
  PasskeyAuthenticationCanceled:
    'パスキーの確認が取り消されました。もう一度お試しください',
  PasskeyRegistrationCanceled: 'パスキーの登録が取り消されました',
  PasskeyAlreadyExists: 'この端末 (認証器) には既にパスキーが登録されています',
  PasskeyNotSupported:
    'このブラウザ・端末はパスキーに対応していません',
  RelyingPartyMismatch: 'このサイトのパスキーとして使えません。アドレスが正しいか確認してください',
  PasskeyOperationAborted: 'パスキーの操作が中断されました。もう一度お試しください',
  PasskeyRetrievalFailed: 'パスキーを確かめられませんでした。もう一度お試しください',
  PasskeyRegistrationFailed: 'パスキーを登録できませんでした。もう一度お試しください',
  InvalidPasskeyAuthenticationOptions: 'パスキーを確かめられませんでした。もう一度お試しください',
  InvalidPasskeyRegistrationOptions: 'パスキーを登録できませんでした。もう一度お試しください',
}

const PASSKEY_ERRORS: ReadonlySet<string> = new Set([
  'PasskeyAuthenticationCanceled',
  'PasskeyNotSupported',
  'RelyingPartyMismatch',
  'PasskeyOperationAborted',
  'PasskeyRetrievalFailed',
  'InvalidPasskeyAuthenticationOptions',
])

// Cognito の既知の文言: https://repost.aws/knowledge-center/cognito-mfa-errors
const SIGN_IN_SESSION_ERRORS: ReadonlySet<string> = new Set([
  'Invalid session for the user',
  'Invalid session for the user, session is expired',
  'Invalid session for the user, session can only be used once',
])

export function isSignInSessionError(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.name === 'NotAuthorizedException' &&
    SIGN_IN_SESSION_ERRORS.has(error.message.trim().replace(/\.$/, ''))
  )
}

/** 辞書に無い Cognito 例外は、原因追跡のために例外名だけ控えめに添える */
export function toJaMessage(
  error: unknown,
  options: { passwordlessSignIn?: boolean; emailCode?: boolean } = {},
): string {
  if (!(error instanceof Error)) {
    return GENERIC_ERROR
  }
  if (options.passwordlessSignIn && isSignInSessionError(error)) {
    return 'サインインの手続きの期限が切れました。もう一度最初からお試しください'
  }
  if (options.emailCode && error.name === 'CodeMismatchException') {
    return '確認コードが違います。いちばん新しいメールのコードを入れてください'
  }
  const known = AUTH_ERROR_MESSAGES[error.name]
  if (known) {
    return known
  }
  if (!error.name || error.name === 'Error') {
    return error.message || GENERIC_ERROR
  }
  return `${GENERIC_ERROR}（${error.name}）`
}

/** パスキーでのサインインが使えなかった失敗 (取り消し・非対応など)。候補に応じて別の入口を案内する */
export function isPasskeySignInError(error: unknown): boolean {
  return error instanceof Error && PASSKEY_ERRORS.has(error.name)
}
