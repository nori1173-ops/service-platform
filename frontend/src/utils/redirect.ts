import type { RouteLocationRaw } from 'vue-router'

/** サインインの後の行き先。同じサイトの絶対パスだけを受け付け、ほかはホームにする (開いたリダイレクトを防ぐ) */
export function safeRedirect(redirect: unknown): RouteLocationRaw {
  const safe =
    typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
  return safe ? redirect : { name: 'home' }
}
