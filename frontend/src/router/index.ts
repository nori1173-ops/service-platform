import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { apps } from '@/apps/registry'
import { installGuards } from './guards'

const appRoutes: RouteRecordRaw[] = apps.map((app) => ({
  path: `/${app.id}`,
  meta: { appId: app.id, requiredGroups: app.requiredGroups },
  children: app.routes,
}))

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { requiresAuth: false, layout: 'auth', title: 'サインイン' },
  },
  {
    path: '/login/admin',
    name: 'login-admin',
    component: () => import('@/views/AdminLoginView.vue'),
    meta: { requiresAuth: false, layout: 'auth', title: '管理者のサインイン' },
  },
  {
    path: '/',
    component: () => import('@/layouts/ShellLayout.vue'),
    meta: { layout: 'shell' },
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/StartView.vue'),
        meta: { title: '入り方の選択' },
      },
      {
        path: 'devices',
        name: 'devices',
        component: () => import('@/views/DeviceLoginView.vue'),
        meta: { title: '通信機ログイン' },
      },
      {
        path: 'devices/:account',
        name: 'device-services',
        component: () => import('@/views/DeviceServicesView.vue'),
        props: true,
        meta: { title: 'サービスの一覧' },
      },
      {
        path: 'account',
        name: 'account',
        component: () => import('@/views/AccountView.vue'),
        meta: { title: 'アカウント' },
      },
      {
        path: 'forbidden',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { title: 'アクセス権限がありません' },
      },
      ...appRoutes,
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: 'ページが見つかりません' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

installGuards(router)

export default router
