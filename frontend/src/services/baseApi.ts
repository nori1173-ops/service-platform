import type { AdminState, Me } from '@/stores/me'
import type { ApiEnvelope } from '@/types'
import type {
  DeviceLoginInput,
  DeviceLoginResult,
  DeviceServices,
  MyAssignment,
  MyDevice,
  RefreshAvailability,
  RefreshStarted,
} from '@/types/devices'
import { createAppApi } from './http'

// 土台のアプリ base (`/api/base`)。停止中などの 403 はアカウントの画面が扱うので、/account へ移さない
const api = createAppApi('base', { redirectOnDenied: false })

function dataOf<T>(envelope: ApiEnvelope<T>): T {
  if (envelope.data === null) {
    throw new Error('応答に data がありません')
  }
  return envelope.data
}

export const baseApi = {
  async getMe(): Promise<Me> {
    const response = await api.get<ApiEnvelope<Me>>('/me')
    return dataOf(response.data)
  },

  async registerTotp(): Promise<{ admin_state: AdminState }> {
    const response = await api.post<ApiEnvelope<{ admin_state: AdminState }>>('/me/totp-registered')
    return dataOf(response.data)
  },

  async enablePasskeyMfa(): Promise<{ passkey_mfa_enabled: boolean }> {
    const response = await api.post<ApiEnvelope<{ passkey_mfa_enabled: boolean }>>('/me/passkey-mfa')
    return dataOf(response.data)
  },

  async getDevices(): Promise<{ devices: MyDevice[] }> {
    const response = await api.get<ApiEnvelope<{ devices: MyDevice[] }>>('/me/devices')
    return dataOf(response.data)
  },
  async getAssignments(): Promise<{ assignments: MyAssignment[] }> {
    const response = await api.get<ApiEnvelope<{ assignments: MyAssignment[] }>>('/me/assignments')
    return dataOf(response.data)
  },

  /** 暗証番号は本文で送る (URL に載せない)。呼び出し元は照会に使ったら捨てる */
  async deviceLogin(input: DeviceLoginInput): Promise<DeviceLoginResult> {
    const response = await api.post<ApiEnvelope<DeviceLoginResult>>('/devices/login', input)
    return dataOf(response.data)
  },

  /** 最新値の開始。サーバは前回値の照会で暗証番号を確かめてから受け付ける。暗証番号は本文で送る */
  async startRefresh(input: { account: string; serial: string; pin: string }): Promise<RefreshStarted> {
    const response = await api.post<ApiEnvelope<RefreshStarted>>('/devices/refresh', {
      account: input.account,
      serial: input.serial,
      pin: input.pin,
    })
    return dataOf(response.data)
  },

  async getRefreshAvailability(account: string): Promise<RefreshAvailability> {
    const response = await api.get<ApiEnvelope<RefreshAvailability>>(
      `/devices/${encodeURIComponent(account)}/refresh-availability`,
    )
    return dataOf(response.data)
  },

  async getDeviceServices(account: string): Promise<DeviceServices> {
    const response = await api.get<ApiEnvelope<DeviceServices>>(`/devices/${encodeURIComponent(account)}/services`)
    return dataOf(response.data)
  },
}
