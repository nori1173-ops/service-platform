import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { adminUsersApi } from '../api'
import { periodFailure } from '../periodLabels'
import type {
  AssignInput,
  DeviceAccessMode,
  DevicePeriod,
  DevicePeriodDetail,
  DevicePeriodFilter,
  GrantInput,
  StartDevicePeriodInput,
} from '../types'
import type { OpResult } from './users'

/** 一覧の行ごとに詳細を読むときの同時の数 (一覧の API は割り当てと許可の要約を返さない) */
const DETAIL_CONCURRENCY = 4

const BUSY = '操作中です。完了を待ってください'

/**
 * 一覧と詳細は毎回サーバから読み直す (台帳の正本は API の強い整合の読み取り)。
 * 操作は 1 つずつ。操作の後は、成否を問わず開いている詳細を読み直す。
 */
export const useDevicePeriodsStore = defineStore('admin-users/device-periods', () => {
  const periods = ref<DevicePeriodDetail[]>([])
  const status = ref<DevicePeriodFilter>('active')
  const loading = ref(false)
  const error = ref<string | null>(null)
  const detail = ref<DevicePeriodDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref<string | null>(null)
  const working = ref(false)
  let listRequest = 0
  let detailRequest = 0
  let detailId: string | null = null

  /** 1 件でも読めなければ一覧は出さないので、まだ始めていない取得はしない */
  async function loadDetails(rows: DevicePeriod[], request: number): Promise<DevicePeriodDetail[]> {
    const loaded: DevicePeriodDetail[] = new Array(rows.length)
    let next = 0
    let failed = false
    const worker = async (): Promise<void> => {
      while (!failed && next < rows.length && request === listRequest) {
        const index = next++
        try {
          loaded[index] = await adminUsersApi.getDevicePeriod(rows[index].device_period_id)
        } catch (e: unknown) {
          failed = true
          throw e
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(DETAIL_CONCURRENCY, rows.length) }, worker))
    return loaded
  }

  /** 最後に頼んだ切り替えの応答だけを一覧にする。読み始めたら前の一覧は捨てる */
  async function fetch(filter: DevicePeriodFilter = status.value): Promise<void> {
    const request = ++listRequest
    status.value = filter
    periods.value = []
    loading.value = true
    error.value = null
    try {
      const response = await adminUsersApi.listDevicePeriods(filter)
      const loaded = await loadDetails(response.device_periods, request)
      if (request === listRequest) {
        // 稼働中の一覧は結果整合の索引から作るので、詳細で終了と分かった行を外す
        periods.value = loaded.filter((row) => filter === 'all' || row.status === 'active')
      }
    } catch (e: unknown) {
      if (request === listRequest) {
        periods.value = []
        error.value = extractApiError(e)?.message ?? '利用期間の一覧を読めませんでした'
      }
    } finally {
      if (request === listRequest) {
        loading.value = false
      }
    }
  }

  /** 最後に頼んだ利用期間の応答だけを詳細にする。別の利用期間を読み始めたら前の詳細は捨てる */
  async function fetchDetail(periodId: string): Promise<void> {
    detailId = periodId
    const request = ++detailRequest
    if (detail.value?.device_period_id !== periodId) {
      detail.value = null
    }
    detailLoading.value = true
    detailError.value = null
    try {
      const loaded = await adminUsersApi.getDevicePeriod(periodId)
      if (request === detailRequest) {
        detail.value = loaded
      }
    } catch (e: unknown) {
      if (request === detailRequest) {
        detail.value = null
        detailError.value = extractApiError(e)?.message ?? '利用期間の詳細を読めませんでした'
      }
    } finally {
      if (request === detailRequest) {
        detailLoading.value = false
      }
    }
  }

  /**
   * 詳細での操作は、その利用期間を開いたままなら読み直す。
   * 開始の成功は一覧を読み直すが、その結果は待たない (開始は POST の応答で確定し、すぐ詳細へ移れる)
   */
  async function operate<T>(periodId: string | null, action: () => Promise<T>): Promise<OpResult<T>> {
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
      if (periodId && detailId === periodId) {
        await fetchDetail(periodId)
      }
      if (!periodId && result.ok) {
        void fetch()
      }
    } finally {
      working.value = false
    }
    return result
  }

  return {
    periods,
    status,
    loading,
    error,
    detail,
    detailLoading,
    detailError,
    working,
    fetch,
    fetchDetail,
    start: (input: StartDevicePeriodInput) => operate(null, () => adminUsersApi.startDevicePeriod(input)),
    end: (periodId: string) => operate(periodId, () => adminUsersApi.endDevicePeriod(periodId)),
    setAccess: (periodId: string, access: DeviceAccessMode) =>
      operate(periodId, () => adminUsersApi.setAccess(periodId, access)),
    assign: (periodId: string, input: AssignInput) => operate(periodId, () => adminUsersApi.assign(periodId, input)),
    endAssignment: (periodId: string, assignmentId: string) =>
      operate(periodId, () => adminUsersApi.endAssignment(periodId, assignmentId)),
    grant: (periodId: string, input: GrantInput) => operate(periodId, () => adminUsersApi.grant(periodId, input)),
    endGrant: (periodId: string, grantId: string) =>
      operate(periodId, () => adminUsersApi.endGrant(periodId, grantId)),
  }
})
