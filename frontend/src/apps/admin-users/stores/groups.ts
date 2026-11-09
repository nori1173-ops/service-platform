import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { adminUsersApi } from '../api'
import { periodFailure } from '../periodLabels'
import type { Group, GroupDetail, GroupInput } from '../types'
import type { OpResult } from './users'

const BUSY = '操作中です。完了を待ってください'

/** サーバの一覧と同じ並び (経路の group_id を # でつないだ文字列の順 = 木の順) */
function byTree(a: Group, b: Group): number {
  const left = a.path.join('#')
  const right = b.path.join('#')
  return left < right ? -1 : left > right ? 1 : 0
}

/** グループの木 (ADR D13)。一覧と詳細は毎回サーバから読み直す。操作は 1 つずつ */
export const useGroupsStore = defineStore('admin-users/groups', () => {
  const groups = ref<Group[]>([])
  const status = ref<'active' | 'all'>('active')
  const loading = ref(false)
  const error = ref<string | null>(null)
  const detail = ref<GroupDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref<string | null>(null)
  const working = ref(false)
  let listRequest = 0
  let detailRequest = 0

  async function fetch(filter: 'active' | 'all' = status.value): Promise<void> {
    const request = ++listRequest
    status.value = filter
    loading.value = true
    error.value = null
    try {
      const response = await adminUsersApi.listGroups(filter)
      if (request === listRequest) {
        groups.value = response.groups
      }
    } catch (e: unknown) {
      if (request === listRequest) {
        groups.value = []
        error.value = extractApiError(e)?.message ?? 'グループの一覧を読めませんでした'
      }
    } finally {
      if (request === listRequest) {
        loading.value = false
      }
    }
  }

  async function fetchDetail(groupId: string): Promise<void> {
    const request = ++detailRequest
    if (detail.value?.group_id !== groupId) {
      detail.value = null
    }
    detailLoading.value = true
    detailError.value = null
    try {
      const loaded = await adminUsersApi.getGroup(groupId)
      if (request === detailRequest) {
        detail.value = loaded
      }
    } catch (e: unknown) {
      if (request === detailRequest) {
        detail.value = null
        detailError.value = extractApiError(e)?.message ?? 'グループの詳細を読めませんでした'
      }
    } finally {
      if (request === detailRequest) {
        detailLoading.value = false
      }
    }
  }

  /**
   * 操作の応答のグループを一覧に反映する。一覧は結果整合の索引から読むので、直後は作ったグループが無い・
   * 古い名前・終えたのに稼働中、がありうる。稼働中の表示では終えたグループを外す。一覧を読めなかったときは足さない
   */
  function reflect(group: Group): void {
    if (error.value !== null) return
    const rest = groups.value.filter((item) => item.group_id !== group.group_id)
    const shown = status.value === 'active' && group.status !== 'active' ? rest : [...rest, group]
    groups.value = shown.sort(byTree)
  }

  /** 操作の後は、成否を問わず一覧と (開いていれば) 詳細を読み直す。グループの行を返す操作は、その行を一覧に反映する */
  async function operate<T>(
    groupId: string | null,
    action: () => Promise<T>,
    reflectRow?: (data: T) => void,
  ): Promise<OpResult<T>> {
    if (working.value) {
      return { ok: false, code: null, message: BUSY }
    }
    working.value = true
    let result: OpResult<T>
    try {
      result = { ok: true, data: await action() }
    } catch (e: unknown) {
      result = periodFailure(e)
    }
    try {
      await fetch()
      if (result.ok && reflectRow) {
        reflectRow(result.data)
      }
      if (groupId && detail.value?.group_id === groupId) {
        await fetchDetail(groupId)
      }
    } finally {
      working.value = false
    }
    return result
  }

  return {
    groups,
    status,
    loading,
    error,
    detail,
    detailLoading,
    detailError,
    working,
    fetch,
    fetchDetail,
    create: (input: GroupInput) => operate(null, () => adminUsersApi.createGroup(input), reflect),
    update: (groupId: string, input: { name: string; note: string | null }) =>
      operate(groupId, () => adminUsersApi.updateGroup(groupId, input), reflect),
    end: (groupId: string) => operate(groupId, () => adminUsersApi.endGroup(groupId), reflect),
    addMember: (groupId: string, userId: string) => operate(groupId, () => adminUsersApi.addMember(groupId, userId)),
    removeMember: (groupId: string, userId: string) =>
      operate(groupId, () => adminUsersApi.removeMember(groupId, userId)),
  }
})
