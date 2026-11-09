import { test, expect } from './helpers/fixtures'

test('入り方の選択からアカウントの割り当てを確認できる', async ({ page }) => {
  // : 1 台 1 サービスのお客様は / で近道するので、アプリの中のホームから入り方の選択を開く
  await page.goto('/account')
  await page.getByRole('link', { name: 'ホーム', exact: true }).click()
  await expect(page.getByRole('heading', { name: '入り方の選択' })).toBeVisible()
  await page.locator('[data-test="start-account"]').click()
  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByText('割り当てられた通信機と許可されたサービス')).toBeVisible()
})
