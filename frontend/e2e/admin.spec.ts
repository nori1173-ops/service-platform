import { test, expect } from './helpers/fixtures'
import { adminSignIn, api, passwordState } from './helpers/auth'
import { baseURL, manifest } from './helpers/environment'

test.use({ storageState: { cookies: [], origins: [] } })

test.setTimeout(120000)

test('管理者がパスワードとTOTPで入り、事前作成利用者を停止・403・復帰する', async ({ page, createContext }) => {
  const target = manifest().users.target
  const context = await createContext({ baseURL, storageState: await passwordState(target) })
  try {
    const targetPage = await context.newPage()
    await targetPage.goto('/account')
    await adminSignIn(page)
    await page.goto(`/admin-users/users/${target.user_id}`)
    await expect(page.locator('[data-test="op-suspend"]')).toBeVisible()
    await page.locator('[data-test="op-suspend"]').click()
    await page.getByRole('textbox', { name: '理由 (任意)', exact: true }).fill('dev E2E の停止確認')
    await page.locator('[data-test="suspend-submit"]').click()
    await expect(page.locator('[data-test="op-restore"]')).toBeVisible()
    const denied = await api(targetPage, '/api/base/me/assignments')
    expect(denied.status).toBe(403)
    expect(denied.body.error.code).toBe('USER_SUSPENDED')
    await page.locator('[data-test="op-restore"]').click()
    await page.locator('[data-test="verify-method-other"]').click()
    await page.getByRole('textbox', { name: '確認のメモ (誰に、何を確かめたか)', exact: true }).fill('dev E2E 用利用者の復帰確認')
    await page.locator('[data-test="verify-submit"]').click()
    await expect(page.locator('[data-test="op-suspend"]')).toBeVisible()
    expect((await api(targetPage, '/api/base/me/assignments')).status).toBe(200)
    const fresh = await createContext({ baseURL, storageState: await passwordState(target) })
    try {
      const restored = await fresh.newPage()
      await restored.goto('/account')
      await expect(restored.getByRole('heading', { name: 'アカウント', exact: true })).toBeVisible()
    } finally { await fresh.close() }
  } finally {
    try {
      if (page.url().startsWith(baseURL)) {
        const result = await api(page, `/api/admin-users/users/${target.user_id}`)
        if (result.status === 200 && result.body.data.status === 'suspended') {
          const restore = await api(page, `/api/admin-users/users/${target.user_id}/restore`, 'POST', { verified_method: 'other', note: 'dev E2E の片付け' })
          expect(restore.status).toBe(200)
        }
      }
    } finally { await context.close() }
  }
})
