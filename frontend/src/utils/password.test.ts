import { describe, expect, it } from 'vitest'
import { PASSWORD_POLICY_MESSAGE, isValidPassword, passwordPolicyRule } from './password'

// infra/core/template.yaml の PasswordPolicy (12 文字以上、英大小・数字、記号は任意) と同じ
describe('isValidPassword', () => {
  it('12 文字以上で英大小・数字を含むと通る', () => {
    expect(isValidPassword('Abcdefghij12')).toBe(true)
  })

  it('記号を含んでも通る', () => {
    expect(isValidPassword('Abcdefghij1!')).toBe(true)
  })

  it('11 文字は落ちる（Cognito の MinimumLength は 12）', () => {
    expect(isValidPassword('Abcdefghi12')).toBe(false)
  })

  it.each([
    ['大文字なし', 'abcdefghij12'],
    ['小文字なし', 'ABCDEFGHIJ12'],
    ['数字なし', 'Abcdefghijkl'],
  ])('%s は落ちる', (_label, value) => {
    expect(isValidPassword(value)).toBe(false)
  })
})

describe('passwordPolicyRule', () => {
  it('規則を満たさないと日本語の規則メッセージを返す', () => {
    expect(passwordPolicyRule('short')).toBe(PASSWORD_POLICY_MESSAGE)
    expect(PASSWORD_POLICY_MESSAGE).toContain('12 文字以上')
  })

  it('規則を満たすと true を返す', () => {
    expect(passwordPolicyRule('Abcdefghij12')).toBe(true)
  })
})
