import { describe, expect, it } from 'vitest'
import { apps, findApp } from '@/apps/registry'
import { RESERVED_PATHS } from '@/apps/types'
import manifest from '../index'
import { routes } from '../routes'

describe('admin-users のアプリマニフェスト', () => {
  it('id が予約パスと衝突せず、レジストリに登録される', () => {
    expect(RESERVED_PATHS).not.toContain(manifest.id)
    expect(apps.map((app) => app.id)).toContain('admin-users')
    expect(findApp('admin-users')).toBe(manifest)
  })

  // 権限の実体はバックエンドだが、admin 以外にメニューを見せない
  it('admin だけに見える', () => {
    expect(manifest.requiredGroups).toEqual(['admin'])
  })

  it('routes が routes.ts の定義そのもので、一覧と詳細の遅延ロードの子ルート', () => {
    expect(manifest.routes).toBe(routes)
    expect(manifest.routes.map((route) => [route.name, route.path])).toEqual([
      ['admin-users.users', ''],
      ['admin-users.user', 'users/:userId'],
      ['admin-users.groups', 'groups'],
      ['admin-users.group', 'groups/:id'],
      ['admin-users.device-periods', 'device-periods'],
      ['admin-users.device-period', 'device-periods/:id'],
    ])
    for (const route of manifest.routes) {
      expect(typeof route.component).toBe('function')
    }
    expect(manifest.routes[1].props).toBe(true)
  })

  it('nav が /admin-users の利用者の一覧を指す', () => {
    expect(manifest.nav).toHaveLength(3)
    expect(manifest.nav[1]).toMatchObject({ to: '/admin-users/groups', title: 'グループ' })
    expect(manifest.nav[2]).toMatchObject({ to: '/admin-users/device-periods', title: '通信機の利用期間' })
    expect(manifest.nav[0].to).toBe('/admin-users')
    expect(manifest.nav[0].title).toBe('利用者の一覧')
  })

  // 呼び名は「利用者」(禁止語を使わない、リポの規約)
  it('サイドバーの名前は「利用者の管理」', () => {
    expect(manifest.title).toBe('利用者の管理')
  })

  it('サイドバーでは業務アプリより下に並ぶ', () => {
    expect(manifest.order).toBe(90)
    expect(manifest.icon).toMatch(/^mdi-/)
  })
})
