import type { DevicePeriodDetail, UserSummary } from '../types'

export const PID = '01JBZ000000000000000000001'
export const UID = '01JBZ000000000000000000002'
export const AID = '01JBZ000000000000000000003'
export const GID = '01JBZ000000000000000000004'
export const START = '2026-11-04T01:00:00Z'
export function period(overrides: Partial<DevicePeriodDetail> = {}): DevicePeriodDetail {
  return {
    device_period_id: PID, account: '00000001', org_name: '組織A', status: 'active',
    started_at: START, started_by: UID, ended_at: null, ended_by: null, note: '確認用',
    access: 'everyone', access_changed_at: START, access_changed_by: null,
    assignments: [{ assignment_id: AID, device_period_id: PID, account: '00000001', user_id: UID,
      valid_from: START, valid_to: null, role: 'user', serial: 'SN-001',
      assigned_by: UID, assigned_at: START, ended_by: null }],
    grants: [{ grant_id: GID, service: 'camera', account: '00000001', starts_at: START,
      ends_at: null, status: 'active', granted_by: UID, granted_at: START, ended_by: null }],
    ...overrides,
  }
}
export function user(overrides: Partial<UserSummary> = {}): UserSummary {
  return { user_id: UID, email: 'user@example.com', status: 'active', kind: 'employee', role: 'admin',
    org_name: '組織A', admin_state: 'active', cognito_status: 'CONFIRMED', cognito_enabled: true,
    created_at: START, updated_at: START, in_ledger: true, ...overrides }
}
export function apiError(code: string): unknown {
  return { response: { status: 409, data: { error: { code, message: 'server message' } } } }
}
export function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (e: unknown) => void } {
  let resolve!: (value: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
