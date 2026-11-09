import type { Router } from 'vue-router'
import { SITE_TITLE, siteEnv } from '@/config/site'
import { useAuthStore } from '@/stores/auth'
import { useLandingStore } from '@/stores/landing'
import { useMeStore } from '@/stores/me'
import { replaceTo } from '@/utils/navigation'
import { isLanding, resolveLanding } from './landing'

/** 検証サイトのタブをタイトルの先頭で見分けられるようにする（UI-12） */
const VERIFICATION_PREFIX = '[検証] '

/** 認証済みユーザーが開く必要のないルート */
const GUEST_ONLY_ROUTE_NAMES: readonly string[] = ['login', 'login-admin']

/** 停止中・admin の準備中 / 確認待ちでも開けるルート (ADR D9 の例外 1・2 を使う画面) */
const RESTRICTED_ROUTE_NAME = 'account'

/** 着地の近道  の印を残すルート (通信機ログインの画面) */
const SHORTCUT_ROUTE_NAME = 'devices'

export function installGuards(router: Router): void {
  /** ナビゲーションの世代。判定の待機中に次のナビゲーションが始まったかを見分ける */
  let navigations = 0

  router.beforeEach(async (to, from) => {
    const navigation = ++navigations
    const auth = useAuthStore()
    if (!auth.initialized) {
      await auth.checkAuth()
    }

    const requiresAuth = to.matched.every((record) => record.meta.requiresAuth !== false)
    if (!auth.isAuthenticated) {
      return requiresAuth ? { name: 'login', query: { redirect: to.fullPath } } : true
    }

    // 画面の出し分けのためだけに読む。権限の実体は各 API が台帳で確かめる (ADR D3)
    const me = useMeStore()
    if (!me.attempted) {
      await me.load()
      if (!auth.isAuthenticated) {
        return { name: 'login', query: { redirect: to.fullPath } }
      }
    }

    const name = typeof to.name === 'string' ? to.name : ''
    if (GUEST_ONLY_ROUTE_NAMES.includes(name)) {
      return me.restricted ? { name: RESTRICTED_ROUTE_NAME } : { name: 'home' }
    }
    if (me.restricted && name !== RESTRICTED_ROUTE_NAME) {
      return { name: RESTRICTED_ROUTE_NAME }
    }

    const requiredGroups = to.matched.flatMap((record) => record.meta.requiredGroups ?? [])
    const allowed = requiredGroups.some((group) => auth.groups.includes(group))
    if (requiredGroups.length > 0 && !allowed) {
      return { name: 'forbidden' }
    }

    // 着地だけ近道する。明示的に開いた /devices・/devices/{account} とアプリの中のホームは今までどおり
    if (name === 'home' && isLanding(from)) {
      const session = auth.session
      const landing = await resolveLanding(me.me?.is_admin === true || auth.groups.includes('admin'))
      // 待機の間に次の画面へ移ったら、この判定の結果を使わない。利用者が替わったら、替わった利用者で判定し直す
      if (navigation !== navigations) {
        return false
      }
      if (!auth.isAuthenticated) {
        return { name: 'login', query: { redirect: to.fullPath } }
      }
      if (session !== auth.session) {
        return to.fullPath
      }
      if (landing.kind === 'login') {
        useLandingStore().enter(landing.account)
        return { name: SHORTCUT_ROUTE_NAME, replace: true }
      }
      if (landing.kind === 'launch') {
        // 別のビルド (/camera/) へ移る。戻るで / に戻ってまた移る往復を避けるため履歴に残さない
        replaceTo(landing.url)
        return false
      }
    }

    return true
  })

  router.afterEach((to, _from, failure) => {
    // 中断・取り消しのナビゲーションでは画面は替わらない。今の画面の印とタイトルを残す
    if (failure) {
      return
    }
    if (to.name !== SHORTCUT_ROUTE_NAME) {
      useLandingStore().clear()
    }
    const title = [to.meta.title, SITE_TITLE].filter(Boolean).join(' | ')
    document.title = siteEnv() === 'dev' ? `${VERIFICATION_PREFIX}${title}` : title
  })
}
