import { DASH, formatDateTime } from '@/utils/format'
import type { AdminState, UserKind, UserStatus, VerifyMethod } from './types'

/** API は UTC の ISO 8601 を返す。`formatDateTime` は壁時計として読むので JST に寄せてから渡す。 */
const JST_OFFSET_MS = 9 * 60 * 60 * 1000

export const KIND_LABELS: Record<UserKind, string> = { customer: 'お客様', employee: '社員' }

export const STATUS_LABELS: Record<UserStatus, string> = { active: '有効', suspended: '停止中' }

export const ADMIN_STATE_LABELS: Record<AdminState, string> = {
  pending: '準備中',
  awaiting: '確認待ち',
  active: '有効',
}

export const VERIFY_METHOD_LABELS: Record<VerifyMethod, string> = {
  phone: '電話',
  in_person: '対面',
  video: 'ビデオ通話',
  other: 'その他',
}

export const VERIFY_METHODS: VerifyMethod[] = ['phone', 'in_person', 'video', 'other']

export const COGNITO_STATUS_LABELS: Record<string, string> = {
  CONFIRMED: '確認済み',
  FORCE_CHANGE_PASSWORD: '一時パスワード',
  RESET_REQUIRED: 'リセット要',
  UNCONFIRMED: '未確認',
}

export function formatJst(value: string | null | undefined): string {
  if (!value) {
    return DASH
  }
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? value : formatDateTime(new Date(parsed + JST_OFFSET_MS).toISOString())
}

export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
export const EMAIL_MAX = 254
export const ORG_NAME_MAX = 100
export const CONTACT_MAX = 200
export const NOTE_MAX = 500
export const REASON_MAX = 500

export function emailRule(value: string): true | string {
  const trimmed = value.trim()
  if (!trimmed) {
    return 'メールアドレスを入力してください'
  }
  return (trimmed.length <= EMAIL_MAX && EMAIL_PATTERN.test(trimmed)) || 'メールアドレスの形が正しくありません'
}

export function maxRule(limit: number): (value: string | null) => true | string {
  return (value) => (value ?? '').trim().length <= limit || `${limit} 文字以内で入力してください`
}
