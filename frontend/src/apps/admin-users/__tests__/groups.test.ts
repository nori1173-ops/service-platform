import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VAutocomplete } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Component } from 'vue'
import { adminUsersApi } from '../api'
import AccessDialog from '../components/AccessDialog.vue'
import AssignDialog from '../components/AssignDialog.vue'
import GroupDialog from '../components/GroupDialog.vue'
import GroupsView from '../views/GroupsView.vue'
import { useGroupsStore } from '../stores/groups'
import type { Group } from '../types'
import { START, UID, user } from './periodFixtures'

vi.mock('../api', () => ({
  adminUsersApi: {
    listGroups: vi.fn(),
    getGroup: vi.fn(),
    createGroup: vi.fn(),
    updateGroup: vi.fn(),
    endGroup: vi.fn(),
    addMember: vi.fn(),
    removeMember: vi.fn(),
    listUsers: vi.fn(),
  },
}))

const ROOT = '01JBZ00000000000000GROUP01'
const CHILD = '01JBZ00000000000000GROUP02'
const GRAND = '01JBZ00000000000000GROUP03'

function group(id: string, name: string, path: string[], overrides: Partial<Group> = {}): Group {
  return {
    group_id: id,
    name,
    parent_group_id: path.length > 1 ? path[path.length - 2] : null,
    path,
    depth: path.length,
    status: 'active',
    note: null,
    created_at: START,
    created_by: UID,
    updated_at: START,
    updated_by: UID,
    ended_at: null,
    ended_by: null,
    ...overrides,
  }
}

let wrapper: VueWrapper
function el(id: string): HTMLElement {
  const found = document.querySelector(`[data-test="${id}"]`) as HTMLElement | null
  if (!found) throw new Error(`[data-test="${id}"] が見つかりません`)
  return found
}
function has(id: string): boolean {
  return document.querySelector(`[data-test="${id}"]`) !== null
}
async function type(id: string, value: string): Promise<void> {
  const input = el(id).querySelector('input,textarea') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}
async function click(id: string): Promise<void> {
  el(id).click()
  await flushPromises()
}
async function open(component: Component, props: Record<string, unknown> = {}): Promise<VueWrapper> {
  wrapper = mount(component, {
    props: { modelValue: true, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
})
afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('グループのダイアログ', () => {
  it('根を作るときは名前とメモだけを送り、段の名前は聞かずに例をヒントで出す', async () => {
    await open(GroupDialog, { mode: 'create' })
    expect(el('group-hint').textContent).toContain('会社 > 部署 > 課')
    expect(el('group-hint').textContent).toContain('県 > 事務所 > 出張所')
    expect(el('group-hint').textContent).toContain('コンサル > 支店')
    await type('group-name', ' 会社A ')
    await type('group-note', ' メモ ')
    await click('group-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ name: '会社A', note: 'メモ' }]])
  })

  it('子を作るときは親の group_id を固定して送る', async () => {
    await open(GroupDialog, { mode: 'create', parent: group(ROOT, '会社A', [ROOT]) })
    expect(document.body.textContent).toContain('親: 会社A')
    await type('group-name', '部署B')
    await click('group-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ name: '部署B', note: null, parent_group_id: ROOT }]])
  })

  it('名前が空か 100 文字を超えれば送らない', async () => {
    await open(GroupDialog, { mode: 'create' })
    await click('group-submit')
    await type('group-name', 'あ'.repeat(101))
    await click('group-submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('名前の変更は今の値から始め、名前とメモを送る', async () => {
    await open(GroupDialog, { mode: 'rename', initialName: '会社A', initialNote: '前のメモ' })
    expect((el('group-name').querySelector('input') as HTMLInputElement).value).toBe('会社A')
    await type('group-name', '会社Z')
    await click('group-submit')
    expect(wrapper.emitted('submit')).toEqual([[{ name: '会社Z', note: '前のメモ' }]])
  })
})

describe('access のダイアログ', () => {
  it('everyone と assigned の意味を出し、選んだ値を送る', async () => {
    await open(AccessDialog, { access: 'everyone', changedAt: START, changedBy: 'admin@example.com' })
    expect(document.body.textContent).toContain('サインインした人なら誰でも一覧で見て、通信機ログインに進めます')
    expect(document.body.textContent).toContain('割り当てた人とグループの人だけが進めます')
    expect(el('access-changed-by').textContent).toContain('admin@example.com')
    await click('access-assigned')
    await click('access-submit')
    expect(wrapper.emitted('submit')).toEqual([['assigned']])
  })

  it('変えた人が無ければ「—」を出し、今と同じ値では送らない', async () => {
    await open(AccessDialog, { access: 'assigned', changedAt: START, changedBy: null })
    expect(el('access-changed-by').textContent).toContain('—')
    await click('access-submit')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})

describe('割り当ての相手', () => {
  it('既定は人で user_id を送る', async () => {
    await open(AssignDialog, { startedAt: START, users: [user()], groups: [group(ROOT, '会社A', [ROOT])] })
    wrapper.findComponent(VAutocomplete).vm.$emit('update:modelValue', UID)
    await flushPromises()
    await click('assign-submit')
    const [[payload]] = wrapper.emitted('submit') as [[Record<string, unknown>]]
    expect(payload).toMatchObject({ target_type: 'user', user_id: UID })
    expect(payload).not.toHaveProperty('group_id')
  })

  it('グループに切り替えると稼働中のグループを経路の名前で選び、group_id を送る', async () => {
    const groups = [
      group(ROOT, '会社A', [ROOT]),
      group(CHILD, '部署B', [ROOT, CHILD]),
      group(GRAND, '終えた課', [ROOT, CHILD, GRAND], { status: 'ended' }),
    ]
    await open(AssignDialog, { startedAt: START, users: [user()], groups })
    await click('assign-target-group')
    const picker = wrapper.findAllComponents(VAutocomplete).find((item) => item.attributes('data-test') === 'assign-group')
    const labels = (picker!.props() as unknown as { items: { label: string }[] }).items.map((item) => item.label)
    expect(labels).toEqual(['会社A', '会社A > 部署B'])
    picker!.vm.$emit('update:modelValue', CHILD)
    await flushPromises()
    await click('assign-submit')
    const [[payload]] = wrapper.emitted('submit') as [[Record<string, unknown>]]
    expect(payload).toMatchObject({ target_type: 'group', group_id: CHILD })
    expect(payload).not.toHaveProperty('user_id')
  })
})

describe('グループのストア (一覧は結果整合の索引なので、操作の応答を反映する)', () => {
  const OTHER = '01JBZ00000000000000GROUP04'

  it('作成の直後に一覧が古くても、作ったグループを木の順の位置に足す', async () => {
    const stale = [group(ROOT, '会社A', [ROOT]), group(OTHER, '会社D', [OTHER])]
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({ groups: stale })
    vi.mocked(adminUsersApi.createGroup).mockResolvedValue({
      ...group(CHILD, '部署B', [ROOT, CHILD]),
      path_names: ['会社A', '部署B'],
    })
    const store = useGroupsStore()
    await store.fetch()
    const result = await store.create({ name: '部署B', note: null, parent_group_id: ROOT })
    expect(result.ok).toBe(true)
    expect(store.groups.map((item) => item.group_id)).toEqual([ROOT, CHILD, OTHER])
    expect(stale.map((item) => item.group_id)).toEqual([ROOT, OTHER])
  })

  it('名前の変更の直後に一覧が古くても、新しい名前の 1 行にする', async () => {
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({ groups: [group(ROOT, '会社A', [ROOT])] })
    vi.mocked(adminUsersApi.updateGroup).mockResolvedValue(group(ROOT, '会社Z', [ROOT], { note: 'メモ' }))
    const store = useGroupsStore()
    await store.fetch()
    await store.update(ROOT, { name: '会社Z', note: 'メモ' })
    expect(store.groups).toHaveLength(1)
    expect(store.groups[0]).toMatchObject({ group_id: ROOT, name: '会社Z', note: 'メモ' })
  })

  it('終えた直後に一覧が古くても、稼働中の表示からは外し、すべての表示では終了にする', async () => {
    const active = [group(ROOT, '会社A', [ROOT]), group(OTHER, '会社D', [OTHER])]
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({ groups: active })
    vi.mocked(adminUsersApi.endGroup).mockResolvedValue(group(OTHER, '会社D', [OTHER], { status: 'ended' }))
    const store = useGroupsStore()
    await store.fetch('active')
    await store.end(OTHER)
    expect(store.groups.map((item) => item.group_id)).toEqual([ROOT])

    await store.fetch('all')
    await store.end(OTHER)
    expect(store.groups.map((item) => [item.group_id, item.status])).toEqual([
      [ROOT, 'active'],
      [OTHER, 'ended'],
    ])
  })

  it('操作が失敗したら読み直した一覧のまま', async () => {
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({ groups: [group(ROOT, '会社A', [ROOT])] })
    vi.mocked(adminUsersApi.createGroup).mockRejectedValue(new Error('network'))
    const store = useGroupsStore()
    await store.fetch()
    const result = await store.create({ name: '部署B', note: null, parent_group_id: ROOT })
    expect(result.ok).toBe(false)
    expect(store.groups.map((item) => item.group_id)).toEqual([ROOT])
  })

  it('一覧を読めなかったときは応答の行を足さない', async () => {
    vi.mocked(adminUsersApi.listGroups).mockRejectedValue(new Error('network'))
    vi.mocked(adminUsersApi.createGroup).mockResolvedValue(group(ROOT, '会社A', [ROOT]))
    const store = useGroupsStore()
    await store.create({ name: '会社A', note: null })
    expect(store.groups).toEqual([])
    expect(store.error).not.toBeNull()
  })
})

describe('グループの一覧', () => {
  async function show(): Promise<void> {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/admin-users/groups', name: 'admin-users.groups', component: GroupsView },
        { path: '/admin-users/groups/:id', name: 'admin-users.group', component: { template: '<div />' } },
      ],
    })
    await router.push('/admin-users/groups')
    await router.isReady()
    wrapper = mount(GroupsView, {
      global: { plugins: [createVuetify({ components, directives }), router] },
      attachTo: document.body,
    })
    await flushPromises()
  }

  it('木の順に段を下げて並べ、3 段目には「子を作る」を出さない', async () => {
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({
      groups: [group(ROOT, '会社A', [ROOT]), group(CHILD, '部署B', [ROOT, CHILD]), group(GRAND, '課C', [ROOT, CHILD, GRAND])],
    })
    await show()
    const rows = [...document.querySelectorAll('[data-test^="group-row-"]')].map((row) => row.getAttribute('data-test'))
    expect(rows).toEqual([`group-row-${ROOT}`, `group-row-${CHILD}`, `group-row-${GRAND}`])
    expect(el(`group-row-${GRAND}`).getAttribute('data-depth')).toBe('3')
    expect(has(`group-child-${ROOT}`)).toBe(true)
    expect(has(`group-child-${CHILD}`)).toBe(true)
    expect(has(`group-child-${GRAND}`)).toBe(false)
  })

  it('根の作成は名前とメモだけを送り、すべてへの切り替えで status=all を読む', async () => {
    vi.mocked(adminUsersApi.listGroups).mockResolvedValue({ groups: [] })
    vi.mocked(adminUsersApi.createGroup).mockResolvedValue(group(ROOT, '会社A', [ROOT]))
    await show()
    await click('group-create-root')
    await type('group-name', '会社A')
    await click('group-submit')
    expect(adminUsersApi.createGroup).toHaveBeenCalledWith({ name: '会社A', note: null })
    await click('groups-filter-all')
    expect(adminUsersApi.listGroups).toHaveBeenLastCalledWith('all')
  })
})
