import { createAppApi } from '@/services/http'
import type { ApiEnvelope } from '@/types'
import type {
  ActivateInput,
  AssignInput,
  DeviceAssignment,
  DevicePeriod,
  DevicePeriodDetail,
  DevicePeriodFilter,
  DeviceAccessMode,
  DevicePeriodsResponse,
  GrantInput,
  Group,
  GroupDetail,
  GroupInput,
  GroupMember,
  InviteInput,
  InviteResult,
  ProfileInput,
  ReinviteInput,
  ReinviteResult,
  RestoreInput,
  ServiceGrant,
  StartDevicePeriodInput,
  TemporaryPasswordResult,
  UserDetail,
  UsersResponse,
} from './types'

// baseURL は /api/admin-users。Bearer 付与と 401 / 403 の後始末は createAppApi が行う（設計 9.7）。
const api = createAppApi('admin-users')

function path(userId: string, suffix = ''): string {
  return `/users/${encodeURIComponent(userId)}${suffix}`
}

function periodPath(periodId: string, suffix = ''): string {
  return `/device-periods/${encodeURIComponent(periodId)}${suffix}`
}

function groupPath(groupId: string, suffix = ''): string {
  return `/groups/${encodeURIComponent(groupId)}${suffix}`
}

function dataOf<T>(envelope: ApiEnvelope<T>): T {
  if (envelope.data === null) {
    throw new Error('応答に data がありません')
  }
  return envelope.data
}

async function get<T>(url: string): Promise<T> {
  return dataOf((await api.get<ApiEnvelope<T>>(url)).data)
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  return dataOf((await api.post<ApiEnvelope<T>>(url, body)).data)
}

async function put<T>(url: string, body: unknown): Promise<T> {
  return dataOf((await api.put<ApiEnvelope<T>>(url, body)).data)
}

export const adminUsersApi = {
  listUsers: () => get<UsersResponse>('/users'),
  getUser: (userId: string) => get<UserDetail>(path(userId)),
  invite: (input: InviteInput) => post<InviteResult>('/users', input),
  updateProfile: (userId: string, input: ProfileInput) => put<unknown>(path(userId, '/profile'), input),
  changeEmail: (userId: string, email: string) => put<unknown>(path(userId, '/email'), { email }),
  suspend: (userId: string, reason: string | null) =>
    post<unknown>(path(userId, '/suspend'), { reason }),
  restore: (userId: string, input: RestoreInput) => post<unknown>(path(userId, '/restore'), input),
  reinvite: (userId: string, input: ReinviteInput) =>
    post<ReinviteResult>(path(userId, '/reinvite'), input),
  activateAdmin: (userId: string, input: ActivateInput) =>
    post<unknown>(path(userId, '/admin-activation'), input),
  /** 一時パスワードは応答で 1 回だけ返る。呼び出し元は表示したら捨てる */
  issueTemporaryPassword: (userId: string) =>
    post<TemporaryPasswordResult>(path(userId, '/temporary-password')),
  revokeAdmin: (userId: string) => put<unknown>(path(userId, '/admin'), { admin: false }),
  listDevicePeriods: (status: DevicePeriodFilter = 'active') =>
    get<DevicePeriodsResponse>(`/device-periods?status=${status}`),
  getDevicePeriod: (periodId: string) => get<DevicePeriodDetail>(periodPath(periodId)),
  startDevicePeriod: (input: StartDevicePeriodInput) => post<DevicePeriod>('/device-periods', input),
  endDevicePeriod: (periodId: string) => post<DevicePeriod>(periodPath(periodId, '/end')),
  assign: (periodId: string, input: AssignInput) =>
    post<DeviceAssignment>(periodPath(periodId, '/assignments'), input),
  endAssignment: (periodId: string, assignmentId: string) =>
    post<DeviceAssignment>(periodPath(periodId, `/assignments/${encodeURIComponent(assignmentId)}/end`)),
  grant: (periodId: string, input: GrantInput) => post<ServiceGrant>(periodPath(periodId, '/grants'), input),
  endGrant: (periodId: string, grantId: string) =>
    post<ServiceGrant>(periodPath(periodId, `/grants/${encodeURIComponent(grantId)}/end`)),
  setAccess: (periodId: string, access: DeviceAccessMode) => put<DevicePeriod>(periodPath(periodId, '/access'), { access }),
  listGroups: (status: 'active' | 'all' = 'active') => get<{ groups: Group[] }>(`/groups?status=${status}`),
  getGroup: (groupId: string) => get<GroupDetail>(groupPath(groupId)),
  createGroup: (input: GroupInput) => post<Group>('/groups', input),
  updateGroup: (groupId: string, input: { name: string; note: string | null }) => put<Group>(groupPath(groupId), input),
  endGroup: (groupId: string) => post<Group>(groupPath(groupId, '/end')),
  addMember: (groupId: string, userId: string) => post<GroupMember>(groupPath(groupId, '/members'), { user_id: userId }),
  removeMember: (groupId: string, userId: string) =>
    post<GroupMember>(groupPath(groupId, `/members/${encodeURIComponent(userId)}/remove`)),
}
