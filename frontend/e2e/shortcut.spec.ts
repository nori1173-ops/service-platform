import type { Page } from '@playwright/test'
import { test, expect } from './helpers/fixtures'
import { baseURL, manifest } from './helpers/environment'
import { adminSignIn, api, passwordState } from './helpers/auth'
import { dummyAccount, endDummyPeriod, startDummyPeriod } from './helpers/dummyDevice'

// : お客様の近道。着地 (読み込んだ直後の / とサインインの直後) だけ、1 台 1 サービスなら途中の画面を飛ばす
test.use({ storageState: { cookies: [], origins: [] } })

test.setTimeout(180000)

interface Listed { account: string; service_count?: number }

async function visibleDevices(page: Page): Promise<Listed[]> {
  const listed = await api(page, '/api/base/me/devices')
  expect(listed.status).toBe(200)
  return listed.body.data.devices
}

async function startedAt(admin: Page, periodId: string): Promise<string> {
  const detail = await api(admin, `/api/admin-users/device-periods/${periodId}`)
  expect(detail.status).toBe(200)
  return detail.body.data.started_at
}

/** 利用者に人宛で割り当てる。有効期間の始まりは利用期間の始まり (それより前は断られる) */
async function assign(admin: Page, periodId: string, userId: string, from: string): Promise<string> {
  const assigned = await api(admin, `/api/admin-users/device-periods/${periodId}/assignments`, 'POST', {
    user_id: userId, valid_from: from,
  })
  expect(assigned.status).toBe(201)
  return assigned.body.data.assignment_id
}

async function grantCamera(admin: Page, periodId: string, from: string): Promise<void> {
  const granted = await api(admin, `/api/admin-users/device-periods/${periodId}/grants`, 'POST', {
    service: 'camera', starts_at: from,
  })
  expect(granted.status).toBe(201)
}

test('近道が効く: 見える通信機が 1 台でサービスが 1 つなら、/ は通信機の暗証番号の段になる', async ({ page, createContext }) => {
  const target = manifest().users.target
  const prefix = `e2e-${manifest().run_id}`
  const targetContext = await createContext({ baseURL, storageState: await passwordState(target) })
  let periodId: string | undefined
  try {
    const targetPage = await targetContext.newPage()
    await targetPage.goto('/account')
    await adminSignIn(page)
    periodId = await startDummyPeriod(page, 'assigned', `${prefix}-org`)
    const from = await startedAt(page, periodId)
    await assign(page, periodId, target.user_id, from)
    await grantCamera(page, periodId, from)
    const devices = await visibleDevices(targetPage)
    test.skip(
      devices.length !== 1,
      `target 役に見える通信機が ${devices.length} 台 (dev に誰でも使える通信機がある)。1 台にならないと近道は確かめられない`,
    )
    expect(devices[0]).toMatchObject({ account: dummyAccount, service_count: 1 })

    await targetPage.goto('/')
    await expect(targetPage).toHaveURL(/\/devices$/)
    await expect(targetPage.locator('[data-test="shortcut-notice"]')).toBeVisible()
    await expect(targetPage.locator('[data-test="selected-account"]')).toHaveText(dummyAccount)
    await expect(targetPage.locator('[data-test="field-pin"]')).toBeVisible()
    await expect(targetPage.locator(`[data-test="assignment-${dummyAccount}"]`)).toHaveCount(0)

    await targetPage.locator('[data-test="shortcut-show-list"]').click()
    await expect(targetPage.locator(`[data-test="assignment-${dummyAccount}"]`)).toBeVisible()

    // 明示的に開いた /devices は今までどおり一覧。アプリの中のホームは入り方の選択 (往復しない)
    await targetPage.goto('/devices')
    await expect(targetPage.locator(`[data-test="assignment-${dummyAccount}"]`)).toBeVisible()
    await expect(targetPage.locator('[data-test="shortcut-notice"]')).toHaveCount(0)
    await targetPage.getByRole('link', { name: 'ホーム', exact: true }).click()
    await expect(targetPage.getByRole('heading', { name: '入り方の選択' })).toBeVisible()
  } finally {
    try {
      if (page.url().startsWith(baseURL)) await endDummyPeriod(page, periodId)
    } finally { await targetContext.close() }
  }
})

test('近道が効かない: 見える通信機が 2 台なら、/ は今までどおり入り方の選択', async ({ page, createContext }) => {
  const target = manifest().users.target
  const prefix = `e2e-${manifest().run_id}`
  const targetContext = await createContext({ baseURL, storageState: await passwordState(target) })
  let periodId: string | undefined
  let deviceAssignment: { periodId: string; assignmentId: string } | undefined
  try {
    const targetPage = await targetContext.newPage()
    await targetPage.goto('/account')
    await adminSignIn(page)
    periodId = await startDummyPeriod(page, 'assigned', `${prefix}-org`)
    const from = await startedAt(page, periodId)
    await assign(page, periodId, target.user_id, from)
    await grantCamera(page, periodId, from)
    let devices = await visibleDevices(targetPage)
    const account = process.env.E2E_DEVICE_ACCOUNT
    if (devices.length < 2 && account && /^\d{8}$/.test(account)) {
      // 検証機器の稼働中の利用期間に target 役を割り当てて 2 台にする (access は変えない)
      const periods = await api(page, '/api/admin-users/device-periods')
      const period = periods.body.data.device_periods.find((item: { account: string }) => item.account === account)
      if (period) {
        const assignmentId = await assign(page, period.device_period_id, target.user_id, new Date(Date.now() - 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'))
        deviceAssignment = { periodId: period.device_period_id, assignmentId }
        devices = await visibleDevices(targetPage)
      }
    }
    test.skip(devices.length < 2, 'target 役に見える通信機が 2 台にならない (E2E_DEVICE_ACCOUNT の稼働中の利用期間が要る)')

    await targetPage.goto('/')
    await expect(targetPage.getByRole('heading', { name: '入り方の選択' })).toBeVisible()
    await expect(targetPage).toHaveURL(new URL('/', baseURL).toString())
    await targetPage.goto('/devices')
    await expect(targetPage.locator(`[data-test="assignment-${dummyAccount}"]`)).toBeVisible()
    await expect(targetPage.locator('[data-test="shortcut-notice"]')).toHaveCount(0)
  } finally {
    try {
      if (deviceAssignment) {
        const ended = await api(
          page,
          `/api/admin-users/device-periods/${deviceAssignment.periodId}/assignments/${deviceAssignment.assignmentId}/end`,
          'POST',
          {},
        )
        expect(ended.status).toBe(200)
      }
      if (page.url().startsWith(baseURL)) await endDummyPeriod(page, periodId)
    } finally { await targetContext.close() }
  }
})
