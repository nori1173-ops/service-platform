/** 通信機ログインとサービスの一覧の型 (`/api/base`。計画 2.6 / 4.5) */

export type AssignmentState = 'current' | 'upcoming' | 'ended'

export interface AssignmentGrant {
  service: string
  name: string
  starts_at: string
  ends_at: string | null
  /** 今が期間内か (false は開始前) */
  current: boolean
}

/** 自分の割り当て (`GET /me/assignments`) */
export interface MyAssignment {
  assignment_id: string
  device_period_id: string
  account: string
  org_name: string | null
  valid_from: string
  valid_to: string | null
  /** 初回の通信機ログインの成功で保存した製造番号の控え。終わった割り当てでは返らない */
  serial: string | null
  /** 割り当ての相手。group はグループ宛 (本人の所属の経路にそのグループがある) */
  target_type: 'user' | 'group'
  group_id: string | null
  /** グループの経路の名前を「 > 」でつないだもの (グループ宛だけ) */
  group_label: string | null
  state: AssignmentState
  grants: AssignmentGrant[]
}

/** 通信機ログインの根拠 (ADR D13 / D14) */
export type DeviceBasis = 'user' | 'group' | 'everyone'

/**
 * 通信機ログインの一覧の 1 行 (`GET /me/devices`)。今有効な割り当てと、everyone の稼働中の通信機。
 * 表示だけで、通信機ログインはサーバが条件を確かめ直す
 */
export interface MyDevice {
  account: string
  device_period_id: string
  org_name: string | null
  basis: DeviceBasis
  /** everyone では null */
  assignment_id: string | null
  group_id: string | null
  group_label: string | null
  valid_from?: string | null
  valid_to?: string | null
  /** 製造番号の控え (人宛は割り当て、グループ宛と everyone は本人の控え)。無ければ初回に入力する */
  serial: string | null
  /** 今有効な許可のサービスの数。着地の近道  の判定だけに使う。無ければ近道しない */
  service_count?: number
}

export type LoginMode = 'previous' | 'latest'

export interface DeviceLoginInput {
  account: string
  serial: string
  pin: string
  mode: LoginMode
  /** 最新値の待機中の照会だけ。開始の応答の値をそのまま返す */
  acceptance_id?: string
  poll_token?: string
}

export interface DeviceSummaryDevice {
  address: number
  address_text: string
  model: string | null
  site: string | null
  action: string | null
}

export interface ServiceLink {
  id: string
  name: string
  url: string
}

export interface DeviceLoginResult {
  account: string
  method: LoginMode
  location_name: string | null
  observation: Record<string, unknown>
  devices: DeviceSummaryDevice[]
  services: ServiceLink[]
}

export interface RefreshAvailability {
  account: string
  available: boolean
  reason: 'in_operation' | 'guard_unavailable' | null
  in_progress: boolean
}

export interface RefreshStarted {
  account: string
  accepted_at: string
  expires_at: string
  timeout_seconds: number
  acceptance_id: string
  /** 待機中の照会に付ける。応答で 1 回だけ返り、サーバは保存しない。画面はメモリだけに持つ */
  poll_token: string
  /** 自分の受付が期限内だったので、読取りを要求し直さずに待機を再開した */
  resumed: boolean
}

export interface DeviceService extends ServiceLink {
  starts_at: string
  ends_at: string | null
}

/** 条件 (1)(2)(3) の後のサービスの一覧 (`GET /devices/{account}/services`) */
export interface DeviceServices {
  account: string
  org_name: string | null
  location_name: string | null
  method: LoginMode | null
  logged_in_at: string | null
  expires_at: string | null
  services: DeviceService[]
}
