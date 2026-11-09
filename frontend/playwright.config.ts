import { defineConfig, devices } from '@playwright/test'
import { baseURL, customerStateFile } from './e2e/helpers/environment'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [['list']],
  timeout: 60000,
  expect: { timeout: 10000 },
  use: {
    ...devices['Desktop Chrome'], baseURL, locale: 'ja-JP', timezoneId: 'Asia/Tokyo',
    trace: 'off', screenshot: 'off', video: 'off',
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    { name: 'chromium', testMatch: /.*\.spec\.ts/,
      use: { storageState: customerStateFile }, dependencies: ['setup'] },
  ],
})
