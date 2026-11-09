import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

export async function readInvitedEmail(
  route: Pick<RouteLocationNormalizedLoaded, 'path' | 'query' | 'hash'>,
  router: Pick<Router, 'replace'>,
  emailRules: readonly ((value: string) => boolean | string)[],
): Promise<string | null> {
  if (!route.hash.startsWith('#email=')) {
    return null
  }
  // route.hash は Vue Router が decode 済み。+ や % を再変換しない
  const email = route.hash.slice('#email='.length)
  const valid = emailRules.every((rule) => rule(email) === true)
  await router.replace({ path: route.path, query: route.query, hash: '' })
  return valid ? email : null
}
