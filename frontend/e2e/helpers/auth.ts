import type { Locator, Page } from '@playwright/test'
import { generateSync } from 'otplib'
import { writeFileSync } from 'node:fs'
import { baseURL, manifest, manifestFile, type E2EUser } from './environment'

interface Tokens { IdToken: string; AccessToken: string; RefreshToken: string }

async function cognito(operation: string, payload: object) {
  const response = await fetch('https://cognito-idp.ap-northeast-1.amazonaws.com/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-amz-json-1.1', 'X-Amz-Target': `AWSCognitoIdentityProviderService.${operation}` },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`Cognito ${operation}: HTTP ${response.status}`)
  return response.json()
}

export async function passwordState(user: E2EUser) {
  const state = manifest()
  let result = await cognito('InitiateAuth', {
    ClientId: state.client_id, AuthFlow: 'USER_AUTH',
    AuthParameters: { USERNAME: user.email, PREFERRED_CHALLENGE: 'PASSWORD', PASSWORD: user.password },
  })
  if (result.ChallengeName === 'SELECT_CHALLENGE') {
    result = await cognito('RespondToAuthChallenge', {
      ClientId: state.client_id, Session: result.Session, ChallengeName: 'SELECT_CHALLENGE',
      ChallengeResponses: { USERNAME: user.email, ANSWER: 'PASSWORD', PASSWORD: user.password },
    })
  }
  const tokens = result.AuthenticationResult as Tokens | undefined
  if (!tokens) throw new Error('E2E 初期 PASSWORD 認証が完了しませんでした')
  const payload = JSON.parse(Buffer.from(tokens.IdToken.split('.')[1]!, 'base64url').toString())
  const prefix = `CognitoIdentityServiceProvider.${state.client_id}`
  const userPrefix = `${prefix}.${payload['cognito:username']}`
  return { cookies: [], origins: [{ origin: new URL(baseURL).origin, localStorage: [
    { name: `${prefix}.LastAuthUser`, value: payload['cognito:username'] as string },
    { name: `${userPrefix}.idToken`, value: tokens.IdToken },
    { name: `${userPrefix}.accessToken`, value: tokens.AccessToken },
    { name: `${userPrefix}.refreshToken`, value: tokens.RefreshToken },
    { name: `${userPrefix}.clockDrift`, value: '0' },
    { name: `${userPrefix}.signInDetails`, value: JSON.stringify({ loginId: user.email, authFlowType: 'USER_AUTH' }) },
  ] }] }
}

export async function secretInput(locator: Locator, value: string) {
  await locator.evaluate((element, secret) => {
    const input = element as HTMLInputElement
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, secret)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }, value)
}

export async function adminSignIn(page: Page) {
  const user = manifest().users.admin
  if (!user.totp_secret) throw new Error('E2E admin の TOTP が未登録です')
  await page.goto('/login/admin')
  await page.getByLabel('メールアドレス', { exact: true }).fill(user.email)
  await secretInput(page.getByLabel('パスワード', { exact: true }), user.password)
  await page.locator('[data-test="password-form"]').getByRole('button', { name: 'サインイン', exact: true }).click()
  const input = page.getByLabel('認証コード（6 桁）', { exact: true })
  await input.waitFor({ state: 'visible' })
  const lastStep = user.totp_last_step ?? 0
  if (Math.floor(Date.now() / 30000) <= lastStep) {
    await new Promise(resolve => setTimeout(resolve, (lastStep + 1) * 30000 - Date.now() + 500))
  }
  const code = generateSync({ secret: user.totp_secret })
  const state = manifest()
  state.users.admin.totp_last_step = Math.floor(Date.now() / 30000)
  writeFileSync(manifestFile, JSON.stringify(state) + '\n', { mode: 0o600 })
  await secretInput(input, code)
  await page.getByRole('button', { name: 'コードを確認', exact: true }).click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'))
}

export async function api(page: Page, path: string, method = 'GET', body?: object) {
  if (!path.startsWith('/api/')) throw new Error('API の相対パスが必要です')
  return page.evaluate(async ({ path, method, body }) => {
    const key = Object.keys(localStorage).find(k => k.endsWith('.idToken'))
    const token = key ? localStorage.getItem(key) : null
    const response = await fetch(path, {
      method, signal: AbortSignal.timeout(15000), headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    const result = await response.json()
    return { status: response.status, body: result }
  }, { path, method, body })
}
