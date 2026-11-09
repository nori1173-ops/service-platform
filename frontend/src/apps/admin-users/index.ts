import { defineApp } from '../types'
import { routes } from './routes'

// admin 専用アプリ。サイドバーの表示もルートの遷移も requiredGroups が制御するが、
// 権限の実体はバックエンド（全 API が require_user → require_admin。ADR D9）にある。
export default defineApp({
  id: 'admin-users',
  title: '利用者の管理',
  icon: 'mdi-account-cog-outline',
  description: '利用者の招待・停止・再招待・有効化と、admin の一時パスワード',
  order: 90,
  requiredGroups: ['admin'],
  routes,
  nav: [
    { title: '利用者の一覧', to: '/admin-users', icon: 'mdi-account-multiple-outline' },
    { title: 'グループ', to: '/admin-users/groups', icon: 'mdi-file-tree-outline' },
    { title: '通信機の利用期間', to: '/admin-users/device-periods', icon: 'mdi-calendar-range' },
  ],
})
