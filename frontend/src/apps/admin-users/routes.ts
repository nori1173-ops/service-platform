import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '',
    name: 'admin-users.users',
    component: () => import('./views/UsersView.vue'),
    meta: { title: '利用者の管理' },
  },
  {
    path: 'users/:userId',
    name: 'admin-users.user',
    component: () => import('./views/UserDetailView.vue'),
    props: true,
    meta: { title: '利用者の詳細' },
  },
  {
    path: 'groups',
    name: 'admin-users.groups',
    component: () => import('./views/GroupsView.vue'),
    meta: { title: 'グループ' },
  },
  {
    path: 'groups/:id',
    name: 'admin-users.group',
    component: () => import('./views/GroupDetailView.vue'),
    props: true,
    meta: { title: 'グループの詳細' },
  },
  {
    path: 'device-periods',
    name: 'admin-users.device-periods',
    component: () => import('./views/DevicePeriodsView.vue'),
    meta: { title: '通信機の利用期間' },
  },
  {
    path: 'device-periods/:id',
    name: 'admin-users.device-period',
    component: () => import('./views/DevicePeriodDetailView.vue'),
    props: true,
    meta: { title: '通信機の利用期間の詳細' },
  },
]
