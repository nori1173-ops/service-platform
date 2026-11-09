import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Component } from 'vue'
import EmailDialog from '../components/EmailDialog.vue'
import InviteDialog from '../components/InviteDialog.vue'
import OneTimeSecretDialog from '../components/OneTimeSecretDialog.vue'
import ProfileDialog from '../components/ProfileDialog.vue'
import ReinviteDialog from '../components/ReinviteDialog.vue'
import SuspendDialog from '../components/SuspendDialog.vue'
import VerifyDialog from '../components/VerifyDialog.vue'

const SECRET = 'TempPassw0rdXyz12345'

async function open(component: Component, props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(component, {
    props: { modelValue: true, ...props },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

function el(id: string): HTMLElement {
  const found = document.querySelector(`[data-test="${id}"]`) as HTMLElement | null
  if (!found) {
    throw new Error(`[data-test="${id}"] が見つかりません`)
  }
  return found
}

async function type(id: string, value: string): Promise<void> {
  const field = el(id)
  const input = (field.matches('input, textarea')
    ? field
    : field.querySelector('input, textarea')) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function click(id: string): Promise<void> {
  el(id).click()
  await flushPromises()
}

async function check(id: string): Promise<void> {
  ;(el(id).querySelector('input') as HTMLInputElement).click()
  await flushPromises()
}

function submitted<T>(wrapper: VueWrapper): T[] {
  return (wrapper.emitted('submit') ?? []).map((args) => args[0] as T)
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('InviteDialog', () => {
  it('お客様は組織名が要り、空の項目は送らない', async () => {
    const wrapper = await open(InviteDialog)
    await type('invite-email', ' New@Example.com ')

    await click('invite-submit')
    expect(submitted(wrapper)).toHaveLength(0)

    await type('invite-org', '株式会社サンプル')
    await click('invite-submit')

    expect(submitted(wrapper)).toEqual([
      { email: 'New@Example.com', kind: 'customer', role: 'user', org_name: '株式会社サンプル' },
    ])
  })

  it('社員を admin として招待できる (連絡先とメモを添えて)', async () => {
    const wrapper = await open(InviteDialog)
    await type('invite-email', 'admin@example.com')
    await click('invite-kind-employee')
    await check('invite-admin')
    await type('invite-contact', '内線 1234')
    await type('invite-note', '情報システムの担当')

    await click('invite-submit')

    expect(submitted(wrapper)).toEqual([
      {
        email: 'admin@example.com',
        kind: 'employee',
        role: 'admin',
        contact: '内線 1234',
        note: '情報システムの担当',
      },
    ])
  })

  // admin は社員で招待する (ADR D4。API も 400 で断る)
  it('お客様には admin を選べない', async () => {
    await open(InviteDialog)

    expect((el('invite-admin').querySelector('input') as HTMLInputElement).disabled).toBe(true)
  })

  it.each([
    ['形の正しくないメールアドレス', 'not-an-email', '株式会社サンプル'],
    ['101 文字の組織名', 'a@example.com', 'あ'.repeat(101)],
  ])('%s は送らない', async (_label, email, org) => {
    const wrapper = await open(InviteDialog)
    await type('invite-email', email)
    await type('invite-org', org)

    await click('invite-submit')

    expect(submitted(wrapper)).toHaveLength(0)
  })

  it('取り消しで閉じる', async () => {
    const wrapper = await open(InviteDialog)

    await click('invite-cancel')

    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})

describe('ReinviteDialog', () => {
  it('理由が要る', async () => {
    const wrapper = await open(ReinviteDialog, { email: 'user@example.com', isAdmin: false })

    await click('reinvite-submit')
    expect(submitted(wrapper)).toHaveLength(0)

    await type('reinvite-reason', '端末の盗難')
    await click('reinvite-submit')

    expect(submitted(wrapper)).toEqual([{ reason: '端末の盗難', role: 'user' }])
  })

  // 受信箱の乗っ取りの疑いでは宛先を変え、別経路の本人確認を記録する (ADR D8 手順 4)
  it('宛先の変更と、再招待の前の別経路の確認を送れる', async () => {
    const wrapper = await open(ReinviteDialog, { email: 'user@example.com', isAdmin: false })
    await type('reinvite-reason', '受信箱の乗っ取りの疑い')
    await type('reinvite-email', 'safe@example.com')
    await click('reinvite-method-phone')
    await type('reinvite-verified-note', '窓口の電話で本人に確認')

    await click('reinvite-submit')

    expect(submitted(wrapper)).toEqual([
      {
        reason: '受信箱の乗っ取りの疑い',
        role: 'user',
        email: 'safe@example.com',
        verified_before: { method: 'phone', note: '窓口の電話で本人に確認' },
      },
    ])
  })

  it('今と同じ宛先は送らない', async () => {
    const wrapper = await open(ReinviteDialog, { email: 'user@example.com', isAdmin: false })
    await type('reinvite-reason', '盗難')
    await type('reinvite-email', 'user@example.com')

    await click('reinvite-submit')

    expect(submitted(wrapper)).toEqual([{ reason: '盗難', role: 'user' }])
  })

  it('パスキーなどが消えることと、admin なら一時パスワードが出ることを案内する', async () => {
    await open(ReinviteDialog, { email: 'admin@example.com', isAdmin: true })

    const text = document.body.textContent ?? ''
    expect(text).toContain('パスキー')
    expect(text).toContain('一時パスワード')
  })
})

describe('VerifyDialog', () => {
  it('確認の方法が要り、方法とメモを送る', async () => {
    const wrapper = await open(VerifyDialog, {
      title: '有効に戻しますか?',
      message: 'user@example.com を有効に戻します。',
      confirmLabel: '有効に戻す',
    })

    await click('verify-submit')
    expect(submitted(wrapper)).toHaveLength(0)

    await click('verify-method-in_person')
    await type('verify-note', '対面で確認')
    await click('verify-submit')

    expect(submitted(wrapper)).toEqual([{ method: 'in_person', note: '対面で確認' }])
  })

  it('メモが空なら方法だけを送る', async () => {
    const wrapper = await open(VerifyDialog, { title: 't', message: 'm', confirmLabel: 'ok' })
    await click('verify-method-video')

    await click('verify-submit')

    expect(submitted(wrapper)).toEqual([{ method: 'video' }])
  })

  it('警告を出す', async () => {
    await open(VerifyDialog, { title: 't', message: 'm', confirmLabel: 'ok', warning: '登録の時刻を確かめる' })

    expect(document.body.textContent).toContain('登録の時刻を確かめる')
  })
})

describe('SuspendDialog', () => {
  it('理由は任意で、空なら null を送る', async () => {
    const wrapper = await open(SuspendDialog, { email: 'user@example.com' })

    await click('suspend-submit')
    await type('suspend-reason', '利用の終了')
    await click('suspend-submit')

    expect(submitted(wrapper)).toEqual([null, '利用の終了'])
  })
})

describe('EmailDialog', () => {
  it('形の正しい、今と違うメールアドレスだけを送る', async () => {
    const wrapper = await open(EmailDialog, { current: 'user@example.com' })

    await type('email-new', 'user@example.com')
    await click('email-submit')
    await type('email-new', 'bad')
    await click('email-submit')
    expect(submitted(wrapper)).toHaveLength(0)

    await type('email-new', 'next@example.com')
    await click('email-submit')

    expect(submitted(wrapper)).toEqual(['next@example.com'])
  })

  // 乗っ取った者に宛先を移されないよう、本人の申し出は別経路で確かめる (ADR の運用)
  it('別経路での確認を案内する', async () => {
    await open(EmailDialog, { current: 'user@example.com' })

    expect(document.body.textContent).toContain('別経路')
  })
})

describe('ProfileDialog', () => {
  it('今の値を入れて開き、3 つの項目を送る (空は消す)', async () => {
    const wrapper = await open(ProfileDialog, {
      kind: 'employee',
      orgName: '情報システム課',
      contact: '内線 1234',
      note: 'メモ',
    })

    await type('profile-contact', '')
    await click('profile-submit')

    expect(submitted(wrapper)).toEqual([{ org_name: '情報システム課', contact: '', note: 'メモ' }])
  })

  it('お客様の組織名は消せない', async () => {
    const wrapper = await open(ProfileDialog, {
      kind: 'customer',
      orgName: '株式会社サンプル',
      contact: null,
      note: null,
    })

    await type('profile-org', '')
    await click('profile-submit')

    expect(submitted(wrapper)).toHaveLength(0)
  })
})

describe('OneTimeSecretDialog', () => {
  it('一時パスワードを出し、1 回だけ表示することを案内する', async () => {
    await open(OneTimeSecretDialog, { secret: SECRET, email: 'admin@example.com' })

    expect(el('one-time-secret').textContent).toContain(SECRET)
    expect(document.body.textContent).toContain('二度と表示できません')
    expect(document.body.textContent).toContain('別経路')
  })

  it('閉じるで update:modelValue を出す', async () => {
    const wrapper = await open(OneTimeSecretDialog, { secret: SECRET, email: 'admin@example.com' })

    await click('one-time-close')

    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})


describe('再招待の役割と理由の監査', () => {
  it.each([SuspendDialog, ReinviteDialog])('理由の欄に監査と秘密の注意を常時表示する', async (component) => {
    await open(component, { email: 'user@example.com', isAdmin: false })

    const text = document.body.textContent ?? ''
    expect(text).toContain('理由は監査に 365 日残ります')
    expect(text).toContain('暗証番号・連絡先・パスワードを書かないでください')
  })

  it('一般社員を admin として再招待できる', async () => {
    const wrapper = await open(ReinviteDialog, { email: 'user@example.com', isAdmin: false, kind: 'employee' })
    await type('reinvite-reason', '役割の変更')
    await click('reinvite-role-admin')
    expect(document.body.textContent).toContain('一時パスワードを 1 回だけ表示')

    await click('reinvite-submit')

    expect(submitted(wrapper)).toEqual([{ reason: '役割の変更', role: 'admin' }])
  })

  it('admin を user として再招待できる', async () => {
    const wrapper = await open(ReinviteDialog, { email: 'admin@example.com', isAdmin: true, kind: 'employee' })
    await type('reinvite-reason', '役割の変更')
    await click('reinvite-role-user')
    expect(document.body.textContent).toContain('本人に新しい招待のメールが届きます')

    await click('reinvite-submit')

    expect(submitted(wrapper)).toEqual([{ reason: '役割の変更', role: 'user' }])
  })

  it('お客様に admin の選択肢を出さない', async () => {
    await open(ReinviteDialog, { email: 'user@example.com', isAdmin: false, kind: 'customer' })

    expect(document.querySelector('[data-test="reinvite-role-admin"]')).toBeNull()
  })
})
