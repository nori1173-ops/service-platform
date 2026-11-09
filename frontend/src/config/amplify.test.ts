import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Amplify } from 'aws-amplify'
import { configureAmplify } from './amplify'

vi.mock('aws-amplify', () => ({ Amplify: { configure: vi.fn() } }))

const USER_POOL_ID = 'ap-northeast-1_EXMPLpool1'
const CLIENT_ID = 'exmplclient1'

function cognitoConfig(): Record<string, unknown> {
  const [config] = vi.mocked(Amplify.configure).mock.calls[0] as [
    { Auth: { Cognito: Record<string, unknown> } },
  ]
  return config.Auth.Cognito
}

describe('configureAmplify', () => {
  beforeEach(() => {
    vi.mocked(Amplify.configure).mockClear()
    vi.stubEnv('VITE_COGNITO_USER_POOL_ID', USER_POOL_ID)
    vi.stubEnv('VITE_COGNITO_CLIENT_ID', CLIENT_ID)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
  })

  it('Amplify.configure に userPoolId と userPoolClientId が渡る', () => {
    configureAmplify()

    expect(Amplify.configure).toHaveBeenCalledTimes(1)
    expect(cognitoConfig()).toMatchObject({
      userPoolId: USER_POOL_ID,
      userPoolClientId: CLIENT_ID,
    })
  })

  it('loginWith.email が true', () => {
    configureAmplify()

    expect(cognitoConfig().loginWith).toEqual({ email: true })
  })

  // 招待制なのでサインアップの設定を持たない (ADR D2)
  it('signUpVerificationMethod と userAttributes を渡さない', () => {
    configureAmplify()

    expect(cognitoConfig()).not.toHaveProperty('signUpVerificationMethod')
    expect(cognitoConfig()).not.toHaveProperty('userAttributes')
  })

  it('環境変数が無いとき configure を呼ばずエラーログを出す', () => {
    vi.stubEnv('VITE_COGNITO_USER_POOL_ID', undefined)
    vi.stubEnv('VITE_COGNITO_CLIENT_ID', undefined)
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    configureAmplify()

    expect(Amplify.configure).not.toHaveBeenCalled()
    expect(consoleError).toHaveBeenCalledTimes(1)
  })
})
