import type { Page } from '@playwright/test'
import { test, expect } from './helpers/fixtures'
import { baseURL, manifest } from './helpers/environment'
import { adminSignIn, api } from './helpers/auth'
import { dummyAccount, endDummyPeriod, startDummyPeriod } from './helpers/dummyDevice'

test.setTimeout(180000)

test('グループ宛の割り当てで、子のグループに属する人の一覧に出て、所属を外すと消える', async ({ page, createContext }) => {
  const customer = manifest().users.customer
  const prefix = `e2e-${manifest().run_id}`
  const adminContext = await createContext({ baseURL, storageState: { cookies: [], origins: [] } })
  let admin: Page | undefined
  let rootId: string | undefined
  let childId: string | undefined
  let periodId: string | undefined
  let assignmentId: string | undefined
  let member = false
  try {
    admin = await adminContext.newPage()
    await adminSignIn(admin)
    // 同じ回 (run_id) の前の失敗で残ったグループは、下の段から終える (E2E が作った名前だけ)
    const leftovers = (await api(admin, '/api/admin-users/groups')).body.data.groups
      .filter((item: { name: string }) => item.name.startsWith(`${prefix}-`))
      .sort((a: { depth: number }, b: { depth: number }) => b.depth - a.depth)
    for (const item of leftovers) {
      expect((await api(admin, `/api/admin-users/groups/${item.group_id}/end`, 'POST', {})).status).toBe(200)
    }
    await admin.goto('/admin-users/groups')
    await admin.locator('[data-test="group-create-root"]').click()
    await expect(admin.locator('[data-test="group-hint"]')).toContainText('会社 > 部署 > 課')
    await admin.locator('[data-test="group-name"] input').fill(`${prefix}-root`)
    await admin.locator('[data-test="group-submit"]').click()
    // 一覧は GSI1 (結果整合) から読むので、作成の後に出るまで待つ
    await expect.poll(async () => {
      const listed = await api(admin!, '/api/admin-users/groups')
      rootId = listed.body.data.groups.find((item: { name: string }) => item.name === `${prefix}-root`)?.group_id
      return rootId
    }, { timeout: 20000 }).toBeTruthy()
    await expect(admin.locator(`[data-test="group-row-${rootId}"]`)).toBeVisible()
    const child = await api(admin, '/api/admin-users/groups', 'POST', { name: `${prefix}-child`, parent_group_id: rootId })
    expect(child.status).toBe(201)
    childId = child.body.data.group_id
    expect(child.body.data.depth).toBe(2)
    const added = await api(admin, `/api/admin-users/groups/${childId}/members`, 'POST', { user_id: customer.user_id })
    expect(added.status).toBe(201)
    member = true
    periodId = await startDummyPeriod(admin, 'assigned', `${prefix}-org`)
    // 有効期間は利用期間の開始 (サーバの時刻) 以後にする
    const startedAt = (await api(admin, `/api/admin-users/device-periods/${periodId}`)).body.data.started_at
    const assigned = await api(admin, `/api/admin-users/device-periods/${periodId}/assignments`, 'POST', {
      target_type: 'group', group_id: rootId, valid_from: startedAt,
    })
    expect(assigned.status).toBe(201)
    assignmentId = assigned.body.data.assignment_id

    await page.goto('/devices')
    const row = page.locator(`[data-test="assignment-${dummyAccount}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText(`グループ: ${prefix}-root`)
    const devices = await api(page, '/api/base/me/devices')
    const listedDevice = devices.body.data.devices.find((item: { account: string }) => item.account === dummyAccount)
    expect(listedDevice.basis).toBe('group')

    const removed = await api(admin, `/api/admin-users/groups/${childId}/members/${customer.user_id}/remove`, 'POST', {})
    expect(removed.status).toBe(200)
    member = false
    await page.goto('/account')
    await page.goto('/devices')
    await expect(page.locator(`[data-test="assignment-${dummyAccount}"]`)).toHaveCount(0)
    const denied = await api(page, `/api/base/devices/${dummyAccount}/refresh-availability`)
    expect(denied.status).toBe(403)
    expect(denied.body.error.code).toBe('DEVICE_NOT_ASSIGNED')
  } finally {
    try {
      if (admin) {
        if (member && childId) {
          await api(admin, `/api/admin-users/groups/${childId}/members/${customer.user_id}/remove`, 'POST', {})
        }
        if (periodId && assignmentId) {
          await api(admin, `/api/admin-users/device-periods/${periodId}/assignments/${assignmentId}/end`, 'POST', {})
        }
        await endDummyPeriod(admin, periodId)
        for (const groupId of [childId, rootId]) {
          if (groupId) expect((await api(admin, `/api/admin-users/groups/${groupId}/end`, 'POST', {})).status).toBe(200)
        }
      }
    } finally { await adminContext.close() }
  }
})
