import { extractApiError } from '@/services/http'
import type { OpResult } from './stores/users'
import type { DeviceAccessMode, DeviceAssignment, DevicePeriodStatus, ServiceGrant } from './types'

/** 許可できるサービス (API の services.py と同じ定義) */
export const SERVICES = [{ id: 'camera', name: 'カメラ画像判定' }]

export function serviceName(id: string): string {
  return SERVICES.find((item) => item.id === id)?.name ?? id
}

export const PERIOD_STATUS_LABELS: Record<DevicePeriodStatus, string> = { active: '稼働中', ended: '終了' }

/** 通信機の利用期間の access (ADR D14) */
export const ACCESS_LABELS: Record<DeviceAccessMode, string> = { everyone: '誰でも', assigned: '割り当てだけ' }
export const ACCESS_DESCRIPTIONS: Record<DeviceAccessMode, string> = {
  everyone: 'サインインした人なら誰でも一覧で見て、通信機ログインに進めます。暗証番号を知っている人だけが入れます',
  assigned: '割り当てた人とグループの人だけが進めます。変えると、割り当ての無い人は次の操作から入れません',
}
export const HANDOVER_NOTE =
  '引き渡し: 新しい暗証番号が効くまで、新しい利用期間は「割り当てだけ」で始めてください (前のお客様が前の暗証番号で入れないように)'

/** グループ宛の割り当ての経路の名前 (「 > 」でつなぐ) */
export function pathLabel(names: (string | null)[] | undefined): string {
  return (names ?? []).filter((name): name is string => !!name).join(' > ')
}

/** 利用期間の API のエラーの案内。CONFLICT は詳細を読み直してからやり直してもらう */
export const PERIOD_ERRORS: Record<string, string> = {
  DEVICE_PERIOD_ACTIVE: 'この通信機には稼働中の利用期間があります。一覧で確かめてください',
  ASSIGNMENT_OVERLAP: '同じ利用者の割り当ての期間が重なっています。有効期間を確かめてください',
  GRANT_OVERLAP: '同じサービスの許可の期間が重なっています。期間を確かめてください',
  CONFLICT: 'ほかの操作で状態が変わりました。詳細を読み直してからやり直してください',
  DEVICE_PERIOD_ENDED: '利用期間はすでに終了しています。詳細を読み直してください',
  ASSIGNMENT_ENDED: '割り当てはすでに終了しています。詳細を読み直してください',
  GRANT_ENDED: '許可はすでに終了しています。詳細を読み直してください',
  TRANSACTION_LIMIT_EXCEEDED: '一括操作の上限を超えています。先に割り当てや許可を終えてからやり直してください',
  GROUP_NOT_FOUND: 'グループが見つかりません。一覧を読み直してください',
  GROUP_ENDED: 'グループは終了しています。一覧を読み直してください',
  GROUP_DEPTH_EXCEEDED: 'グループは 3 段までです。3 段目の下には作れません',
  GROUP_NAME_TAKEN: '同じ親の下に同じ名前のグループがあります。別の名前にしてください',
  GROUP_IN_USE: '稼働中の子のグループ・所属・終わっていない割り当てがあるため、終えられません',
  MEMBER_EXISTS: 'この利用者はすでにグループに属しています',
  MEMBER_NOT_FOUND: 'この利用者はグループに属していません。詳細を読み直してください',
}

const OPERATION_FAILED = '操作できませんでした。状態を確かめてからやり直してください'

export function periodFailure(error: unknown): Extract<OpResult<never>, { ok: false }> {
  const api = extractApiError(error)
  const known = api?.code ? PERIOD_ERRORS[api.code] : undefined
  return { ok: false, code: api?.code ?? null, message: known ?? api?.message ?? OPERATION_FAILED }
}

/** 終わりの時刻は期間に含まない (API と同じ)。null は利用期間の終わりまで */
export function isEnded(end: string | null, now = Date.now()): boolean {
  return end !== null && Date.parse(end) <= now
}

export function isAssignmentEnded(assignment: DeviceAssignment, now = Date.now()): boolean {
  return isEnded(assignment.valid_to, now)
}

/** 詳細の「終える」の判定。開始前の許可も終えられる */
export function isGrantEnded(grant: ServiceGrant, now = Date.now()): boolean {
  return grant.status === 'ended' || isEnded(grant.ends_at, now)
}

/** 今有効な許可 (開始 <= 今 < 終了。API の条件 (4) と同じ)。一覧の「許可」の列に使う */
export function isGrantActive(grant: ServiceGrant, now = Date.now()): boolean {
  return grant.status === 'active' && Date.parse(grant.starts_at) <= now && !isEnded(grant.ends_at, now)
}

const JST_OFFSET_MS = 9 * 60 * 60 * 1000
const JST_INPUT = /^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2})(:[0-9]{2}(\.0+)?)?$/

/** UTC の ISO 8601 を datetime-local の入力欄の値 (JST、秒まで) にする。実行環境のタイムゾーンによらない */
export function toJstInput(iso: string): string {
  return new Date(Date.parse(iso) + JST_OFFSET_MS).toISOString().slice(0, 19)
}

/** 開始の入力の初期値。利用期間の開始と今の遅いほう */
export function initialStartInput(startedAt: string, now = Date.now()): string {
  return toJstInput(new Date(Math.max(now, Date.parse(startedAt))).toISOString())
}

/** 入力欄の値 (JST) を API に送る ISO 8601 (+09:00) にする。日付として正しくなければ null */
export function jstToIso(value: string): string | null {
  const parts = JST_INPUT.exec(value)
  if (!parts) {
    return null
  }
  const seconds = `${parts[1]}${parts[2] ? parts[2].slice(0, 3) : ':00'}`
  const iso = `${seconds}+09:00`
  return Number.isFinite(Date.parse(iso)) && toJstInput(iso) === seconds ? iso : null
}

/** 入力の期間の検査 (開始は利用期間の開始以後、終了は開始より後)。問題が無ければ null */
export function intervalError(start: string, end: string, startedAt: string): string | null {
  const from = jstToIso(start)
  if (!from) {
    return '開始の日時を日本時間 (JST) で入力してください'
  }
  if (Date.parse(from) < Date.parse(startedAt)) {
    return '開始は利用期間の開始以後にしてください'
  }
  const to = end ? jstToIso(end) : null
  if (end && !to) {
    return '終了の日時を日本時間 (JST) で入力してください'
  }
  if (to && Date.parse(to) <= Date.parse(from)) {
    return '終了は開始より後にしてください'
  }
  return null
}
