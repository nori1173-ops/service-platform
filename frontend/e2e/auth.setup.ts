import { test as setup, expect } from './helpers/fixtures'
import { writeFileSync, chmodSync } from 'node:fs'
import { passwordState } from './helpers/auth'
import { baseURL, customerStateFile, manifest } from './helpers/environment'

setup('メールを送らずお客様の初期セッションを用意する', async ({ createContext }) => {
  const state = await passwordState(manifest().users.customer)
  writeFileSync(customerStateFile, JSON.stringify(state) + '\n', { mode: 0o600 })
  chmodSync(customerStateFile, 0o600)
  const context = await createContext({ baseURL, storageState: state })
  try {
    const page = await context.newPage()
    await page.goto('/account')
    await expect(page.getByRole('heading', { name: 'アカウント', exact: true })).toBeVisible()
  } finally { await context.close() }
})
