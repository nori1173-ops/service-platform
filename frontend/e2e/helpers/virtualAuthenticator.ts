import type { Page } from '@playwright/test'

export async function virtualAuthenticator(page: Page) {
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('WebAuthn.enable')
  const add = async () => (await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
      hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  })).authenticatorId
  let authenticatorId = await add()
  return {
    async credentials() { return (await cdp.send('WebAuthn.getCredentials', { authenticatorId })).credentials },
    async replace() {
      await cdp.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId })
      authenticatorId = await add()
    },
    async remove() {
      await cdp.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId })
      await cdp.send('WebAuthn.disable')
      await cdp.detach()
    },
  }
}
