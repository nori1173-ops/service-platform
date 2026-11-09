import type { Page } from '@playwright/test'
import { expect } from './fixtures'
import { api } from './auth'

/** E2E 用の通信機アカウント (専用のダミー値)。device-backend には送らない */
export const dummyAccount = process.env.E2E_DUMMY_ACCOUNT ?? '00000001'
if (!/^\d{8}$/.test(dummyAccount)) throw new Error('E2E_DUMMY_ACCOUNT は半角数字 8 桁にしてください')

/** 稼働中の利用期間が残っていないことを確かめてから始める (前の失敗の残りを黙って使わない) */
export async function startDummyPeriod(admin: Page, access: 'everyone' | 'assigned', orgName: string): Promise<string> {
  const listed = await api(admin, '/api/admin-users/device-periods')
  expect(listed.status).toBe(200)
  const left = listed.body.data.device_periods.find((item: { account: string }) => item.account === dummyAccount)
  if (left) throw new Error(`${dummyAccount} に稼働中の利用期間が残っています。先に終えてください`)
  const started = await api(admin, '/api/admin-users/device-periods', 'POST', {
    account: dummyAccount, org_name: orgName, access,
  })
  expect(started.status).toBe(201)
  expect(started.body.data.access).toBe(access)
  return started.body.data.device_period_id
}

export async function endDummyPeriod(admin: Page, periodId: string | undefined): Promise<void> {
  if (!periodId) return
  const ended = await api(admin, `/api/admin-users/device-periods/${periodId}/end`, 'POST', {})
  expect([200, 409]).toContain(ended.status)
}
