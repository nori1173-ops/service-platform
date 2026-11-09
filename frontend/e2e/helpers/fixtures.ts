import { test as base, expect } from '@playwright/test'
import type { Browser, BrowserContext } from '@playwright/test'

type SecurityErrors = { csp: number; refresh: number }

function monitorSecurity(context: BrowserContext, errors: SecurityErrors) {
  context.on('page', page => {
    page.on('console', message => {
      if (/Content Security Policy|violat.*directive/i.test(message.text())) errors.csp++
      if (message.text().includes('RefreshTokenReuseException')) errors.refresh++
    })
    page.on('pageerror', error => {
      if (error.message.includes('RefreshTokenReuseException')) errors.refresh++
    })
  })
}

export const test = base.extend<{
  securityCheck: SecurityErrors
  createContext: Browser['newContext']
}>({
  securityCheck: [async ({ context }, use) => {
    const errors = { csp: 0, refresh: 0 }
    monitorSecurity(context, errors)
    await use(errors)
    expect(errors.csp, 'CSP の違反件数').toBe(0)
    expect(errors.refresh, '更新トークンの再利用エラー件数').toBe(0)
  }, { auto: true }],
  createContext: async ({ browser, securityCheck }, use) => {
    const contexts: BrowserContext[] = []
    try {
      await use(async options => {
        const context = await browser.newContext(options)
        contexts.push(context)
        monitorSecurity(context, securityCheck)
        return context
      })
    } finally {
      await Promise.all(contexts.map(context => context.close()))
    }
  },
})
export { expect }
