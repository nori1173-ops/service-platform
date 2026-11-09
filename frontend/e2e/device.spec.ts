import type { Page } from '@playwright/test'
import { test, expect } from './helpers/fixtures'
import { baseURL, manifest } from './helpers/environment'
import { adminSignIn, api, secretInput } from './helpers/auth'

const pin = process.env.E2E_DEVICE_PIN

test.setTimeout(120000)

test('通信機の前回値からサービス一覧を開き、別タブでも同じセッションを使う', async ({ page, context, createContext }) => {
  test.skip(!pin, 'E2E_DEVICE_PIN 未指定。専用機器の前回値試験は任意実行')
  const account = process.env.E2E_DEVICE_ACCOUNT
  if (!account || !/^\d{8}$/.test(account)) throw new Error('E2E_DEVICE_ACCOUNT に検証機器を設定してください')
  const adminContext = await createContext({ baseURL, storageState: { cookies: [], origins: [] } })
  let admin: Page | undefined
  let periodId: string | undefined
  let assignmentId: string | undefined
  try {
    admin = await adminContext.newPage()
    await adminSignIn(admin)
    const periods = await api(admin, '/api/admin-users/device-periods')
    expect(periods.status).toBe(200)
    const period = periods.body.data.device_periods.find((item: { account: string }) => item.account === account)
    if (!period) throw new Error('検証機器の稼働中の利用期間を先に用意してください')
    periodId = period.device_period_id
    const assigned = await api(admin, `/api/admin-users/device-periods/${periodId}/assignments`, 'POST', {
      user_id: manifest().users.customer.user_id, valid_from: new Date(Date.now() - 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'),
    })
    expect(assigned.status).toBe(201)
    assignmentId = assigned.body.data.assignment_id
    // 明示的に開いた /devices は一覧を出す ( の近道は / の着地だけ)
    await page.goto('/devices')
    const assignment = page.locator(`[data-test="assignment-${account}"]`)
    await expect(assignment).toBeVisible()
    await assignment.click()
    const serial = page.locator('[data-test="field-serial"] input')
    if (await serial.isVisible()) {
      if (!process.env.E2E_DEVICE_SERIAL) throw new Error('初回は E2E_DEVICE_SERIAL が必要です')
      await serial.fill(process.env.E2E_DEVICE_SERIAL)
    }
    await secretInput(page.locator('[data-test="field-pin"] input'), pin!)
    await page.locator('[data-test="load-previous"]').click()
    await expect(page).toHaveURL(new RegExp(`/devices/${account}$`))
    expect((await api(page, `/api/base/devices/${account}/services`)).status).toBe(200)
    const second = await context.newPage()
    await second.goto(`/devices/${account}`)
    expect((await api(second, '/api/base/me')).status).toBe(200)
  } finally {
    try {
      if (admin && periodId && assignmentId) {
        const ended = await api(admin, `/api/admin-users/device-periods/${periodId}/assignments/${assignmentId}/end`, 'POST', {})
        expect(ended.status).toBe(200)
      }
    } finally { await adminContext.close() }
  }
})

test('everyone の検証機器は割り当てを作らずに前回値で通信機ログインできる', async ({ page }) => {
  test.skip(!pin, 'E2E_DEVICE_PIN 未指定。専用機器の前回値試験は任意実行')
  const account = process.env.E2E_DEVICE_ACCOUNT
  if (!account || !/^\d{8}$/.test(account)) throw new Error('E2E_DEVICE_ACCOUNT に検証機器を設定してください')
  const devices = await (async () => {
    await page.goto('/devices')
    return api(page, '/api/base/me/devices')
  })()
  const listed = devices.body.data.devices.find((item: { account: string }) => item.account === account)
  test.skip(!listed || listed.basis !== 'everyone', '検証機器の利用期間が everyone ではない (access を変えない)')
  await page.locator(`[data-test="everyone-${account}"]`).click()
  const serial = page.locator('[data-test="field-serial"] input')
  if (await serial.isVisible()) {
    if (!process.env.E2E_DEVICE_SERIAL) throw new Error('初回は E2E_DEVICE_SERIAL が必要です')
    await serial.fill(process.env.E2E_DEVICE_SERIAL)
  }
  await secretInput(page.locator('[data-test="field-pin"] input'), pin!)
  await page.locator('[data-test="load-previous"]').click()
  await expect(page).toHaveURL(new RegExp(`/devices/${account}$`))
})
