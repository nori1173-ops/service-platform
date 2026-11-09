import { test, expect } from './helpers/fixtures'
import { adminSignIn, api } from './helpers/auth'
import { manifest } from './helpers/environment'
import { virtualAuthenticator } from './helpers/virtualAuthenticator'

test.use({ storageState: { cookies: [], origins: [] } })
test.setTimeout(180000)

test('管理者がTOTPで入りパスキーを登録し、通常の入口から管理APIを呼べる', async ({ page }, testInfo) => {
  const authenticator = await virtualAuthenticator(page)
  const rows = page.locator('[data-test^="passkey-row-"]')
  const state = manifest()
  try {
    await test.step('パスワードとTOTPでサインインする', async () => {
      await adminSignIn(page)
      await page.goto('/account')
      await expect(page.locator('[data-test="passkey-add"]')).toBeVisible()
      await expect(page.locator('[data-test="passkey-empty"], [data-test^="passkey-row-"]')).not.toHaveCount(0)
      while (await rows.count() > 0) {
        const count = await rows.count()
        await rows.first().getByRole('button', { name: '削除', exact: true }).click()
        await page.locator('[data-test="confirm-ok"]').click()
        await expect(rows).toHaveCount(count - 1)
      }
    }, { timeout: 45000 })
    await test.step('パスキーの登録後にMFAとして有効にする', async () => {
      const enabled = page.waitForResponse(response => response.url().endsWith('/api/base/me/passkey-mfa') && response.request().method() === 'POST')
      await page.locator('[data-test="passkey-add"]').click()
      expect((await enabled).status()).toBe(200)
      await expect(rows).toHaveCount(1)
      expect((await authenticator.credentials()).length).toBe(1)
    }, { timeout: 30000 })

    await test.step('要素を選ばず候補を確かめる', async () => {
      // USER_AUTH の候補だけを照会する。応答のセッションを持ち出さず、要素も選ばない。
      const selection = await page.evaluate(async ({ clientId, username }) => {
        const response = await fetch('https://cognito-idp.ap-northeast-1.amazonaws.com/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-amz-json-1.1', 'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth' },
          body: JSON.stringify({ ClientId: clientId, AuthFlow: 'USER_AUTH', AuthParameters: { USERNAME: username } }),
          signal: AbortSignal.timeout(15000),
        })
        const result = await response.json()
        return { status: response.status, challenge: result.ChallengeName, factors: result.AvailableChallenges ?? [] }
      }, { clientId: state.client_id, username: state.users.admin.email })
      expect(selection.status).toBe(200)
      expect(selection.challenge).toBe('SELECT_CHALLENGE')
      expect(selection.factors).toContain('WEB_AUTHN')
      expect(selection.factors).toContain('PASSWORD')
      expect(selection.factors).not.toContain('EMAIL_OTP')
      console.info('admin passkey factors:', JSON.stringify(selection.factors))
      await testInfo.attach('admin-auth-factors', { body: JSON.stringify(selection), contentType: 'application/json' })
    })

    await test.step('通常の入口からパスキーで入り管理APIを呼ぶ', async () => {
      await page.locator('[data-test="account-signout"]').click()
      await expect(page).toHaveURL(/\/login\/admin$/)
      await page.goto('/login')
      await page.getByLabel('メールアドレス', { exact: true }).fill(state.users.admin.email)
      await page.locator('[data-test="start-passkey"]').click()
      await expect(page.getByRole('heading', { name: '入り方の選択' })).toBeVisible()
      expect((await api(page, '/api/admin-users/users')).status).toBe(200)
    }, { timeout: 30000 })

    await test.step('パスキーを残したままパスワードとTOTPでも管理APIを呼ぶ', async () => {
      await page.goto('/account')
      await page.locator('[data-test="account-signout"]').click()
      await expect(page).toHaveURL(/\/login\/admin$/)
      await adminSignIn(page)
      expect((await api(page, '/api/admin-users/users')).status).toBe(200)
    }, { timeout: 45000 })

    await test.step('パスキー削除後もパスワードとTOTPで入る', async () => {
      await page.goto('/account')
      await rows.first().getByRole('button', { name: '削除', exact: true }).click()
      await page.locator('[data-test="confirm-ok"]').click()
      await expect(rows).toHaveCount(0)
      await page.locator('[data-test="account-signout"]').click()
      await expect(page).toHaveURL(/\/login\/admin$/)
      await adminSignIn(page)
      expect((await api(page, '/api/admin-users/users')).status).toBe(200)
    }, { timeout: 45000 })
  } finally {
    if (!page.isClosed()) await authenticator.remove()
  }
})
