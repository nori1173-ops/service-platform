import { test, expect } from './helpers/fixtures'
import { virtualAuthenticator } from './helpers/virtualAuthenticator'
import { api, passwordState } from './helpers/auth'
import { baseURL, manifest } from './helpers/environment'

test('2つのパスキーを登録し、サインアウト・削除後も残りで入れる', async ({ createContext }) => {
  const context = await createContext({ baseURL, storageState: await passwordState(manifest().users.customer) })
  try {
    const page = await context.newPage()
    const first = await virtualAuthenticator(page)
    const rows = page.locator('[data-test^="passkey-row-"]')
    const signIn = async () => {
      await page.getByLabel('メールアドレス', { exact: true }).fill(manifest().users.customer.email)
      await page.locator('[data-test="start-passkey"]').click()
      await page.waitForURL(url => !url.pathname.startsWith('/login'))
      expect((await api(page, '/api/base/me')).status).toBe(200)
    }
    try {
      await page.goto('/account')
      await page.locator('[data-test="passkey-add"]').click()
      await expect(rows).toHaveCount(1)
      const firstRow = await rows.first().getAttribute('data-test')
      const credentials = await first.credentials()
      expect(credentials.length).toBe(1)
      expect(credentials[0]!.rpId).toBe('service-platform-dev.example-cloud.com')
      await first.replace()
      await page.locator('[data-test="passkey-add"]').click()
      await expect(rows).toHaveCount(2)
      expect((await first.credentials()).length).toBe(1)
      await page.locator('[data-test="appbar-signout"]').click()
      await expect(page).toHaveURL(/\/login/)
      await signIn()
      await page.goto('/account')
      await page.locator(`[data-test="${firstRow}"]`).getByRole('button', { name: '削除', exact: true }).click()
      await page.locator('[data-test="confirm-ok"]').click()
      await expect(rows).toHaveCount(1)
      await page.locator('[data-test="appbar-signout"]').click()
      await expect(page).toHaveURL(/\/login/)
      await signIn()
    } finally {
      await first.remove()
    }
  } finally { await context.close() }
})
