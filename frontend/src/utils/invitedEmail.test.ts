import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { readInvitedEmail } from './invitedEmail'

const EMAIL = 'member@example.com'
const StubView = { template: '<div />' }
const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
]

let router: Router

beforeEach(() => {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', component: StubView },
      { path: '/login/admin', component: StubView },
    ],
  })
})

describe('readInvitedEmail', () => {
  it.each([EMAIL, 'member+alerts&news@example.com', 'member%26team@example.com', 'member=name@example.com'])(
    'Vue Router がデコードしたアドレス %s を再変換せずに返す',
    async (email) => {
      await router.push('/login#email=' + encodeURIComponent(email))

      const result = await readInvitedEmail(router.currentRoute.value, router, emailRules)

      expect(result).toBe(email)
      expect(router.currentRoute.value.hash).toBe('')
    },
  )

  it.each(['', 'not-an-email', '@example.com', 'user@', 'user@example', 'user name@example.com', 'user@example.com\n'])(
    '検証を通らない値 %j は null を返し、hash を消す',
    async (email) => {
      await router.push('/login#email=' + encodeURIComponent(email))

      const result = await readInvitedEmail(router.currentRoute.value, router, emailRules)

      expect(result).toBeNull()
      expect(router.currentRoute.value.hash).toBe('')
    },
  )

  it.each([false, 'このアドレスは利用できません'])(
    '呼び出し元のルールが %j を返すアドレスは採用しない',
    async (validationResult) => {
      await router.push('/login#email=' + encodeURIComponent(EMAIL))
      const rule = vi.fn(() => validationResult)

      const result = await readInvitedEmail(router.currentRoute.value, router, [...emailRules, rule])

      expect(rule).toHaveBeenCalledWith(EMAIL)
      expect(result).toBeNull()
      expect(router.currentRoute.value.hash).toBe('')
    },
  )

  it.each(['/login', '/login/admin'])('パス %s と query を残して履歴を置き換える', async (path) => {
    await router.push(path + '?redirect=%2Faccount&registered=1#email=' + encodeURIComponent(EMAIL))
    const replace = vi.spyOn(router, 'replace')

    await readInvitedEmail(router.currentRoute.value, router, emailRules)

    expect(replace).toHaveBeenCalledOnce()
    expect(replace).toHaveBeenCalledWith({
      path,
      query: { redirect: '/account', registered: '1' },
      hash: '',
    })
    expect(router.currentRoute.value.path).toBe(path)
    expect(router.currentRoute.value.query).toEqual({ redirect: '/account', registered: '1' })
    expect(router.currentRoute.value.hash).toBe('')
  })

  it.each(['', '#help', '#email', '#emailOther=member@example.com'])(
    '招待用ではない hash %j はそのままにし、null を返す',
    async (hash) => {
      await router.push('/login' + hash)
      const replace = vi.spyOn(router, 'replace')
      const rule = vi.fn(() => true)

      const result = await readInvitedEmail(router.currentRoute.value, router, [rule])

      expect(result).toBeNull()
      expect(router.currentRoute.value.hash).toBe(hash)
      expect(replace).not.toHaveBeenCalled()
      expect(rule).not.toHaveBeenCalled()
    },
  )
})
