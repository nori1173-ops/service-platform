import type { Page } from '@playwright/test'
import { test, expect } from './helpers/fixtures'
import { baseURL, manifest } from './helpers/environment'
import { adminSignIn, api, passwordState } from './helpers/auth'
import { dummyAccount, endDummyPeriod, startDummyPeriod } from './helpers/dummyDevice'

test.use({ storageState: { cookies: [], origins: [] } })

test.setTimeout(180000)

test('everyone の通信機は割り当ての無い人の一覧に出て、assigned にすると消える (403)', async ({ page, createContext }) => {
  const target = manifest().users.target
  const prefix = `e2e-${manifest().run_id}`
  const targetContext = await createContext({ baseURL, storageState: await passwordState(target) })
  let periodId: string | undefined
  try {
    const targetPage: Page = await targetContext.newPage()
    await targetPage.goto('/account')
    await adminSignIn(page)
    periodId = await startDummyPeriod(page, 'everyone', `${prefix}-org`)
    // everyone の一覧は GSI1 の DEVICE (結果整合) から作るので、API に出る・消えるのを待ってから画面を見る
    const listed = async (): Promise<boolean> => {
      const response = await api(targetPage, '/api/base/me/devices')
      expect(response.status).toBe(200)
      return response.body.data.devices.some(
        (item: { account: string; basis: string }) => item.account === dummyAccount && item.basis === 'everyone',
      )
    }
    await expect.poll(listed, { timeout: 20000 }).toBe(true)

    await targetPage.goto('/devices')
    const row = targetPage.locator(`[data-test="everyone-${dummyAccount}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText(`${prefix}-org`)
    const available = await api(targetPage, `/api/base/devices/${dummyAccount}/refresh-availability`)
    expect(available.status).not.toBe(403)

    await page.goto(`/admin-users/device-periods/${periodId}`)
    await expect(page.locator('[data-test="period-access"]')).toContainText('誰でも')
    await page.locator('[data-test="access-open"]').click()
    await page.locator('[data-test="access-assigned"]').click()
    await page.locator('[data-test="access-submit"]').click()
    await expect(page.locator('[data-test="period-access"]')).toContainText('割り当てだけ')
    await expect.poll(listed, { timeout: 20000 }).toBe(false)

    await targetPage.goto('/account')
    await targetPage.goto('/devices')
    await expect(targetPage.locator(`[data-test="everyone-${dummyAccount}"]`)).toHaveCount(0)
    const denied = await api(targetPage, `/api/base/devices/${dummyAccount}/refresh-availability`)
    expect(denied.status).toBe(403)
    expect(denied.body.error.code).toBe('DEVICE_NOT_ASSIGNED')
  } finally {
    try {
      if (page.url().startsWith(baseURL)) await endDummyPeriod(page, periodId)
    } finally { await targetContext.close() }
  }
})
