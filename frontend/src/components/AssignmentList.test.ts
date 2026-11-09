import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { baseApi } from '@/services/baseApi'
import type { MyAssignment } from '@/types/devices'
import AssignmentList from './AssignmentList.vue'

vi.mock('@/services/baseApi', () => ({
  baseApi: { getAssignments: vi.fn() },
}))

function assignment(overrides: Partial<MyAssignment> = {}): MyAssignment {
  return {
    assignment_id: 'A1',
    device_period_id: 'P1',
    account: '12345678',
    org_name: '組織A',
    valid_from: '2026-11-01T00:00:00Z',
    valid_to: null,
    serial: '87654321',
    target_type: 'user',
    group_id: null,
    group_label: null,
    state: 'current',
    grants: [
      { service: 'camera', name: 'カメラ画像判定', starts_at: '2026-11-01T00:00:00Z', ends_at: null, current: true },
    ],
    ...overrides,
  }
}

let wrapper: VueWrapper

async function show(): Promise<VueWrapper> {
  wrapper = mount(AssignmentList, { global: { plugins: [createVuetify({ components, directives })] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.resetAllMocks()
})

afterEach(() => {
  wrapper?.unmount()
})

// ADR D5 のアカウント単位: 割り当てられた通信機アカウントと許可されたサービスの一覧
describe('AssignmentList', () => {
  it('今有効・これから・終わった割り当てを、期間 (JST) と許可されたサービスと一緒に出す', async () => {
    vi.mocked(baseApi.getAssignments).mockResolvedValue({
      assignments: [
        assignment(),
        assignment({
          assignment_id: 'A2',
          account: '23456789',
          org_name: '組織B',
          state: 'upcoming',
          valid_from: '2026-12-01T00:00:00Z',
          grants: [
            {
              service: 'camera',
              name: 'カメラ画像判定',
              starts_at: '2026-12-01T00:00:00Z',
              ends_at: null,
              current: false,
            },
          ],
        }),
        assignment({
          assignment_id: 'A3',
          account: '34567890',
          state: 'ended',
          valid_to: '2026-10-31T15:00:00Z',
          grants: [],
          serial: null,
        }),
      ],
    })

    await show()

    const current = wrapper.get('[data-test="assignment-A1"]')
    expect(current.text()).toContain('今有効')
    expect(current.text()).toContain('12345678')
    expect(current.text()).toContain('組織A')
    expect(current.text()).toContain('2026-11-01 09:00')
    expect(current.text()).toContain('カメラ画像判定')
    const upcoming = wrapper.get('[data-test="assignment-A2"]')
    expect(upcoming.text()).toContain('これから')
    expect(upcoming.text()).toContain('開始前')
    const ended = wrapper.get('[data-test="assignment-A3"]')
    expect(ended.text()).toContain('終了')
    expect(ended.text()).toContain('2026-11-01 00:00')
    expect(ended.classes()).toContain('text-medium-emphasis')
  })

  it('グループ宛の割り当てにはグループの経路を出す', async () => {
    vi.mocked(baseApi.getAssignments).mockResolvedValue({
      assignments: [assignment({ target_type: 'group', group_id: 'G1', group_label: '会社A > 部署B' })],
    })

    await show()

    expect(wrapper.text()).toContain('グループ: 会社A > 部署B')
  })

  it('割り当てが無ければ案内を出す', async () => {
    vi.mocked(baseApi.getAssignments).mockResolvedValue({ assignments: [] })

    await show()

    expect(wrapper.get('[data-test="assignments-empty"]').text()).toContain('割り当てられた通信機はありません')
  })

  it('読めなければ読み直せる', async () => {
    vi.mocked(baseApi.getAssignments).mockRejectedValueOnce(new Error('Network Error'))
    vi.mocked(baseApi.getAssignments).mockResolvedValueOnce({ assignments: [assignment()] })
    await show()
    expect(wrapper.find('[data-test="assignments-error"]').exists()).toBe(true)

    await wrapper.get('[data-test="assignments-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="assignment-A1"]').exists()).toBe(true)
  })
})
