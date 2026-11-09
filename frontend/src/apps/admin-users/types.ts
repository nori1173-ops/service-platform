/** 利用者の管理の API (`/api/admin-users`、計画 2.6 / 4.2) の型。識別子は台帳の user_id */

export type UserStatus = 'active' | 'suspended'
export type UserKind = 'customer' | 'employee'
export type UserRole = 'user' | 'admin'
export type AdminState = 'pending' | 'awaiting' | 'active'
/** 別経路の確認の方法 (API と scripts で同じ語) */
export type VerifyMethod = 'phone' | 'in_person' | 'video' | 'other'

/** 一覧の 1 件。Cognito にいて台帳に無い利用者は `in_ledger: false` で `user_id` などが null */
export interface UserSummary {
  user_id: string | null
  email: string | null
  status: UserStatus | null
  kind: UserKind | null
  role: UserRole | null
  org_name: string | null
  admin_state: AdminState | null
  /** Cognito の `UserStatus` (CONFIRMED / FORCE_CHANGE_PASSWORD など) */
  cognito_status: string | null
  cognito_enabled: boolean
  /** UTC の ISO 8601。画面では JST に直して表示する */
  created_at: string | null
  updated_at: string | null
  in_ledger: boolean
}

export interface UsersResponse {
  users: UserSummary[]
  /** 呼び出し元自身の user_id。自分の行の操作を止めるのに使う */
  self_user_id: string
}

/** パスワード・パスキー・メールのコードの有無 (AdminGetUserAuthFactors) */
export interface AuthFactors {
  password: boolean
  web_authn: boolean
  email_otp: boolean
}

export interface Assignment {
  account: string | null
  device_period_id: string | null
  assignment_id: string | null
  valid_from: string | null
  valid_to: string | null
  org_name: string | null
}

export interface UserDetail extends UserSummary {
  contact: string | null
  note: string | null
  /** Cognito の admin グループ */
  is_admin: boolean
  /** AdminGetUser の UserMFASettingList に TOTP があるか */
  totp: boolean
  factors: AuthFactors | null
  mfa_enabled_at: string | null
  admin_verified_at: string | null
  admin_verified_method: VerifyMethod | null
  restore_requires: 'reinvite' | null
  reinvite_in_progress: boolean
  email_pending: string | null
  reinvite_count: number
  suspended_at: string | null
  suspend_reason: string | null
  assignments: Assignment[]
  /** 所属するグループ 。古いサーバの応答には無い */
  memberships?: Membership[]
}

export interface InviteInput {
  email: string
  kind: UserKind
  role: UserRole
  org_name?: string
  contact?: string
  note?: string
}

/** admin の招待だけ `temporary_password` を 1 回だけ返す */
export interface InviteResult {
  user_id: string
  email: string
  kind: UserKind
  role: UserRole
  status: UserStatus
  admin_state: AdminState | null
  temporary_password?: string
}

export interface ProfileInput {
  org_name?: string
  contact?: string
  note?: string
}

export interface VerifiedBefore {
  method: VerifyMethod
  note?: string
}

export interface ReinviteInput {
  reason: string
  /** 省略時は現在の役割。admin を選べるのは社員だけ */
  role?: UserRole
  /** 宛先を変えるとき (ADR D8 手順 4) */
  email?: string
  /** 再招待の前の別経路の本人確認 (手順 4) */
  verified_before?: VerifiedBefore
}

/** admin の再招待だけ `temporary_password` を 1 回だけ返す */
export interface ReinviteResult {
  user_id: string
  email: string
  status: UserStatus
  temporary_password?: string
}

export interface RestoreInput {
  verified_method: VerifyMethod
  note?: string
}

export interface ActivateInput {
  method: VerifyMethod
  note?: string
}

export interface TemporaryPasswordResult {
  user_id: string
  temporary_password: string
}

export interface VerifyPayload {
  method: VerifyMethod
  note?: string
}

/** 通信機の利用期間 (計画 2.1 / 2.6、ADR D4)。日時は UTC の ISO 8601 で、画面では JST に直す */
export type DevicePeriodStatus = 'active' | 'ended'
export type DevicePeriodFilter = 'active' | 'all'
/** 通信機の利用期間の access (ADR D14)。属性の無い行もサーバが everyone で返す */
export type DeviceAccessMode = 'everyone' | 'assigned'
export type AssignTarget = 'user' | 'group'

export interface DevicePeriod {
  device_period_id: string
  /** 通信機アカウント (半角数字 8 桁) */
  account: string
  org_name: string
  status: DevicePeriodStatus
  started_at: string
  started_by: string | null
  ended_at: string | null
  ended_by: string | null
  note: string | null
  access: DeviceAccessMode
  access_changed_at: string | null
  /** 変えた人の user_id。属性の無い既存の行は null */
  access_changed_by: string | null
}

/** 割り当て。終えた行も消さずに返る (valid_to が過去) */
export interface DeviceAssignment {
  assignment_id: string
  device_period_id: string
  account: string
  /** 割り当ての相手。target_type の無い既存の行もサーバが user で返す */
  target_type?: AssignTarget
  /** 人宛だけ */
  user_id: string | null
  /** グループ宛だけ */
  group_id?: string | null
  /** グループ宛だけ。根からの名前 */
  group_path_names?: (string | null)[]
  valid_from: string
  /** null は利用期間の終わりまで。終わりの時刻は含まない */
  valid_to: string | null
  role: string
  /** 初回の通信機ログインで保存する製造番号 */
  serial: string | null
  assigned_by: string
  assigned_at: string
  ended_by: string | null
}

/** サービスの許可。終えた行も消さずに返る */
export interface ServiceGrant {
  grant_id: string
  service: string
  account: string
  starts_at: string
  /** null は利用期間の終わりまで。終わりの時刻は含まない */
  ends_at: string | null
  status: DevicePeriodStatus
  granted_by: string
  granted_at: string
  ended_by: string | null
}

export interface DevicePeriodDetail extends DevicePeriod {
  assignments: DeviceAssignment[]
  grants: ServiceGrant[]
}

export interface DevicePeriodsResponse {
  device_periods: DevicePeriod[]
}

export interface StartDevicePeriodInput {
  account: string
  org_name: string
  note?: string
  access: DeviceAccessMode
}

/** 日時はタイムゾーン付きの ISO 8601 (秒まで)。人宛は user_id、グループ宛は group_id だけを送る */
export interface AssignInput {
  target_type: AssignTarget
  user_id?: string
  group_id?: string
  valid_from: string
  valid_to: string | null
}

export type GroupStatus = 'active' | 'ended'

/** グループ (ADR D13)。木は 3 段まで。段の名前は持たない */
export interface Group {
  group_id: string
  name: string
  parent_group_id: string | null
  /** 根から自分までの group_id */
  path: string[]
  depth: number
  status: GroupStatus
  note: string | null
  created_at: string
  created_by: string
  updated_at: string | null
  updated_by: string | null
  ended_at: string | null
  ended_by: string | null
  /** 作成・変更・詳細の応答だけ */
  path_names?: (string | null)[]
}

export interface GroupMember {
  membership_id: string
  user_id: string
  email: string | null
  kind: string | null
  org_name: string | null
  status: string | null
  added_at: string
  added_by: string
}

export interface GroupAssignment {
  assignment_id: string
  device_period_id: string
  account: string
  org_name: string | null
  valid_from: string
  valid_to: string | null
  state: 'current' | 'upcoming' | 'ended'
}

export interface GroupDetail extends Group {
  path_names: (string | null)[]
  children: { group_id: string; name: string; status: GroupStatus }[]
  members: GroupMember[]
  assignments: GroupAssignment[]
}

export interface GroupInput {
  name: string
  note: string | null
  parent_group_id?: string
}

export interface Membership {
  membership_id: string
  group_id: string
  path: string[]
  path_names: (string | null)[]
  added_at: string
}

export interface GrantInput {
  service: string
  starts_at: string
  ends_at: string | null
}
