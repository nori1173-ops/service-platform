import { describe, expect, it, vi } from 'vitest'
import router from './index'

vi.mock('@/apps/registry', () => ({
  apps: [
    {
      id: 'demo-app',
      title: 'デモ',
      icon: 'mdi-cog',
      requiredGroups: ['demo-admin'],
      routes: [{ path: 'items', name: 'demo-items', component: { template: '<div />' } }],
      nav: [],
    },
  ],
  findApp: () => undefined,
}))

describe('router', () => {
  it('アプリのルートが /{app}/... で解決され meta にアプリ情報が乗る', () => {
    const resolved = router.resolve('/demo-app/items')

    expect(resolved.name).toBe('demo-items')
    expect(resolved.meta.appId).toBe('demo-app')
    expect(resolved.meta.requiredGroups).toEqual(['demo-admin'])
    expect(router.resolve('/apps/demo-app/items').name).toBe('not-found')
  })

  it.each([
    ['/login', 'login'],
    ['/login/admin', 'login-admin'],
  ])('%s は未認証で開ける auth レイアウトのルート', (path, name) => {
    const resolved = router.resolve(path)

    expect(resolved.name).toBe(name)
    expect(resolved.meta.requiresAuth).toBe(false)
    expect(resolved.meta.layout).toBe('auth')
  })

  it('/account はサインインの要るシェルの中のルート', () => {
    const resolved = router.resolve('/account')

    expect(resolved.name).toBe('account')
    expect(resolved.matched.every((record) => record.meta.requiresAuth !== false)).toBe(true)
    expect(resolved.matched[0].meta.layout).toBe('shell')
    expect(router.resolve('/').meta.requiresAuth).toBeUndefined()
  })

  // ADR D5: 入り方の選択 → 通信機ログイン → サービスの一覧
  it.each([
    ['/', 'home'],
    ['/devices', 'devices'],
    ['/devices/12345678', 'device-services'],
  ])('%s はサインインの要るシェルの中のルート (%s)', (path, name) => {
    const resolved = router.resolve(path)

    expect(resolved.name).toBe(name)
    expect(resolved.matched.every((record) => record.meta.requiresAuth !== false)).toBe(true)
    expect(resolved.matched[0].meta.layout).toBe('shell')
  })

  it('サービスの一覧は通信機アカウントを props で受ける', () => {
    const resolved = router.resolve('/devices/12345678')

    expect(resolved.params.account).toBe('12345678')
    expect(resolved.matched.at(-1)?.props.default).toBe(true)
  })

  it('入り方の選択の画面を / に置く (HomeView を置き換えた)', async () => {
    const record = router.resolve('/').matched.at(-1)
    const component = record?.components?.default as () => Promise<{ default: { __name?: string } }>

    expect((await component()).default.__name).toBe('StartView')
  })

  // 招待制なのでサインアップとパスワードの再設定の画面は持たない (ADR D2 / D1)
  it.each(['/signup', '/forgot-password'])('%s は無い', (path) => {
    expect(router.resolve(path).name).toBe('not-found')
  })
})
