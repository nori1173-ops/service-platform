<script setup lang="ts">
import { computed, onActivated, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useMeStore } from '@/stores/me'
import { useUiStore } from '@/stores/ui'
import { DASH } from '@/utils/format'
import AccessDialog from '../components/AccessDialog.vue'
import AssignDialog from '../components/AssignDialog.vue'
import EndConfirmDialog from '../components/EndConfirmDialog.vue'
import GrantDialog from '../components/GrantDialog.vue'
import { formatJst } from '../labels'
import {
  ACCESS_DESCRIPTIONS,
  ACCESS_LABELS,
  HANDOVER_NOTE,
  PERIOD_STATUS_LABELS,
  isAssignmentEnded,
  isGrantEnded,
  pathLabel,
  serviceName,
} from '../periodLabels'
import { useDevicePeriodsStore } from '../stores/devicePeriods'
import { useGroupsStore } from '../stores/groups'
import { useUsersStore, type OpResult } from '../stores/users'
import type { AssignInput, DeviceAccessMode, DeviceAssignment, GrantInput, ServiceGrant } from '../types'

const props = defineProps<{ id: string }>()

const TARGET_CHANGED = '表示している利用期間が変わったため、操作を取りやめました。詳細を確かめてからやり直してください'

type Dialog = 'assign' | 'grant' | 'end' | 'confirm' | 'access' | null

/** 操作を開いた時点の利用期間。実行はこの利用期間に送る (後から届いた別の詳細で入れ替わらない) */
interface Target {
  periodId: string
  account: string
  orgName: string
  startedAt: string
}

interface PendingConfirm {
  title: string
  message: string
  warning: string
  confirmLabel: string
  success: string
  run: (periodId: string) => Promise<OpResult<unknown>>
}

const store = useDevicePeriodsStore()
const usersStore = useUsersStore()
const groupsStore = useGroupsStore()
const meStore = useMeStore()
const ui = useUiStore()

const dialog = ref<Dialog>(null)
const target = ref<Target | null>(null)
const confirmAction = ref<PendingConfirm | null>(null)
const dialogError = ref<string | null>(null)

// URL の利用期間の詳細だけを出す。別の利用期間へ移る間は前の詳細と操作を出さない
const period = computed(() => (store.detail?.device_period_id === props.id ? store.detail : null))
const active = computed(() => period.value?.status === 'active')
const busy = computed(() => store.detailLoading || store.working)
const selfUserId = computed(() => meStore.me?.user_id ?? usersStore.selfUserId)
const usersReady = computed(() => !usersStore.loading && !usersStore.error)
const emails = computed(
  () => new Map(usersStore.users.flatMap((user) => (user.user_id ? [[user.user_id, user.email] as const] : []))),
)

// 開いているものを先に、それぞれ開始の新しい順に並べる (終えた行も履歴として残す)
const assignments = computed(() =>
  [...(period.value?.assignments ?? [])].sort(
    (a, b) => Number(isAssignmentEnded(a)) - Number(isAssignmentEnded(b)) || b.valid_from.localeCompare(a.valid_from),
  ),
)
const grants = computed(() =>
  [...(period.value?.grants ?? [])].sort(
    (a, b) => Number(isGrantEnded(a)) - Number(isGrantEnded(b)) || b.starts_at.localeCompare(a.starts_at),
  ),
)

function userLabel(userId: string | null): string {
  return userId ? (emails.value.get(userId) ?? userId) : DASH
}

function targetLabel(assignment: DeviceAssignment): string {
  return assignment.target_type === 'group'
    ? `グループ: ${pathLabel(assignment.group_path_names) || assignment.group_id || DASH}`
    : userLabel(assignment.user_id)
}

function rangeLabel(from: string, to: string | null): string {
  return `${formatJst(from)} 〜 ${to ? formatJst(to) : '利用期間の終わりまで'}`
}

function closeAll(): void {
  dialog.value = null
  target.value = null
  confirmAction.value = null
  dialogError.value = null
}

function closeDialog(value: boolean): void {
  if (!value) {
    closeAll()
  }
}

function openDialog(name: Exclude<Dialog, null>): boolean {
  const current = period.value
  if (!current || busy.value) {
    return false
  }
  target.value = {
    periodId: current.device_period_id,
    account: current.account,
    orgName: current.org_name,
    startedAt: current.started_at,
  }
  dialogError.value = null
  dialog.value = name
  return true
}

function load(): void {
  void store.fetchDetail(props.id)
  void usersStore.fetch()
  void groupsStore.fetch('active')
}

onActivated(load)
watch(
  () => props.id,
  () => {
    closeAll()
    load()
  },
)

/** 開いた時点の利用期間に送る。その後に URL の利用期間が変わっていたら送らない。操作中は重ねない */
async function execute<T>(action: (periodId: string) => Promise<OpResult<T>>): Promise<OpResult<T> | null> {
  const current = target.value
  if (!current || current.periodId !== props.id) {
    closeAll()
    ui.notify(TARGET_CHANGED, 'error')
    return null
  }
  if (busy.value) {
    return null
  }
  dialogError.value = null
  return action(current.periodId)
}

/** 入力のダイアログは、断られたら閉じずに理由を出す (入力を直してやり直せるように) */
async function submitInput<T>(action: (periodId: string) => Promise<OpResult<T>>, success: string): Promise<void> {
  const result = await execute(action)
  if (!result) {
    return
  }
  if (result.ok) {
    closeAll()
    ui.notify(success, 'success')
    return
  }
  dialogError.value = result.message
  if (!period.value) {
    ui.notify(result.message, 'error')
  }
}

function assign(input: AssignInput): void {
  void submitInput((periodId) => store.assign(periodId, input), '割り当てました')
}

function changeAccess(access: DeviceAccessMode): void {
  void submitInput((periodId) => store.setAccess(periodId, access), '通信機ログインに進める人を変えました')
}

function grant(input: GrantInput): void {
  void submitInput((periodId) => store.grant(periodId, input), '許可を出しました')
}

async function finish<T>(action: (periodId: string) => Promise<OpResult<T>>, success: string): Promise<void> {
  const result = await execute(action)
  if (!result) {
    return
  }
  closeAll()
  ui.notify(result.ok ? success : result.message, result.ok ? 'success' : 'error')
}

function endPeriod(): void {
  void finish((periodId) => store.end(periodId), '利用期間を終えました。引き渡しの手順の 2 と 3 を忘れずに行ってください')
}

function askEndAssignment(assignment: DeviceAssignment): void {
  const action: PendingConfirm = {
    title: '割り当てを終えますか?',
    message: `${targetLabel(assignment)} の割り当て (${rangeLabel(assignment.valid_from, assignment.valid_to)}) を今の時刻で終えます。`,
    warning:
      assignment.target_type === 'group'
        ? 'グループの人は、ほかの根拠 (人宛の割り当てか「誰でも」) が無ければ次の操作から入れなくなります。行は消さずに履歴として残ります。'
        : 'この利用者のこの通信機へのログインも切れます。行は消さずに履歴として残ります。付け直すときは新しく割り当てます。',
    confirmLabel: '割り当てを終える',
    success: '割り当てを終えました',
    run: (periodId) => store.endAssignment(periodId, assignment.assignment_id),
  }
  if (openDialog('confirm')) {
    confirmAction.value = action
  }
}

function askEndGrant(grantRow: ServiceGrant): void {
  const action: PendingConfirm = {
    title: '許可を終えますか?',
    message: `${serviceName(grantRow.service)} の許可 (${rangeLabel(grantRow.starts_at, grantRow.ends_at)}) を今の時刻で終えます。`,
    warning: 'この利用期間の利用者は、このサービスを使えなくなります。行は消さずに履歴として残ります。',
    confirmLabel: '許可を終える',
    success: '許可を終えました',
    run: (periodId) => store.endGrant(periodId, grantRow.grant_id),
  }
  if (openDialog('confirm')) {
    confirmAction.value = action
  }
}

function confirm(): void {
  const action = confirmAction.value
  if (action) {
    void finish(action.run, action.success)
  }
}
</script>

<template>
  <div>
    <div class="mb-2">
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-arrow-left"
        :to="{ name: 'admin-users.device-periods' }"
      >
        通信機の利用期間の一覧へ
      </v-btn>
    </div>

    <v-alert
      v-if="store.detailError"
      data-test="detail-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.detailError }}
    </v-alert>

    <v-progress-linear
      :active="store.detailLoading"
      indeterminate
      color="primary"
      class="mb-2"
    />

    <v-skeleton-loader
      v-if="!period && store.detailLoading"
      data-test="detail-loading"
      type="heading, table-tbody"
      class="mb-4"
    />

    <template v-if="period">
      <div class="d-flex align-center flex-wrap ga-2 mb-2">
        <h1 class="text-h5 mb-0">
          {{ period.account }}
        </h1>
        <span class="text-h6 text-medium-emphasis">{{ period.org_name }}</span>
        <v-chip
          data-test="period-status"
          :color="active ? 'success' : 'grey'"
          size="small"
        >
          {{ PERIOD_STATUS_LABELS[period.status] }}
        </v-chip>
      </div>
      <p class="text-body-2 text-medium-emphasis mb-1">
        開始 {{ formatJst(period.started_at) }}
        <template v-if="period.ended_at">
          ・終了 {{ formatJst(period.ended_at) }}
        </template>
        (日本時間)
      </p>
      <p
        v-if="period.note"
        class="text-body-2 text-pre-wrap mb-1"
      >
        {{ period.note }}
      </p>
      <div class="d-flex align-center flex-wrap ga-2 my-2">
        <span class="text-body-2">通信機ログインに進める人</span>
        <v-chip
          data-test="period-access"
          size="small"
          :color="period.access === 'assigned' ? 'primary' : undefined"
        >
          {{ ACCESS_LABELS[period.access ?? 'everyone'] }}
        </v-chip>
        <span class="text-caption text-medium-emphasis">
          {{ formatJst(period.access_changed_at) }} ・ {{ userLabel(period.access_changed_by ?? null) }}
        </span>
        <v-btn
          v-if="active"
          data-test="access-open"
          variant="text"
          size="small"
          color="primary"
          :disabled="busy"
          @click="openDialog('access')"
        >
          変える
        </v-btn>
      </div>
      <p class="text-caption text-medium-emphasis mb-1">
        {{ ACCESS_DESCRIPTIONS[period.access ?? 'everyone'] }}
      </p>
      <p class="text-caption text-medium-emphasis mb-1">
        {{ HANDOVER_NOTE }}
      </p>

      <v-alert
        v-if="usersStore.error"
        data-test="users-error"
        type="warning"
        variant="tonal"
        density="compact"
        class="my-3"
      >
        利用者の一覧を読めないため、割り当てられません: {{ usersStore.error }}
      </v-alert>

      <div
        v-if="active"
        class="d-flex flex-wrap ga-2 my-4"
      >
        <v-btn
          data-test="assign-open"
          variant="tonal"
          color="primary"
          prepend-icon="mdi-account-plus-outline"
          :disabled="busy || !usersReady"
          @click="openDialog('assign')"
        >
          割り当てる
        </v-btn>
        <v-btn
          data-test="grant-open"
          variant="tonal"
          color="primary"
          prepend-icon="mdi-key-plus"
          :disabled="busy"
          @click="openDialog('grant')"
        >
          許可を出す
        </v-btn>
        <v-spacer />
        <v-btn
          data-test="end-period"
          variant="tonal"
          color="warning"
          prepend-icon="mdi-calendar-remove-outline"
          :disabled="busy"
          @click="openDialog('end')"
        >
          利用期間を終える
        </v-btn>
      </div>

      <v-card
        variant="flat"
        border
        class="mb-4 mt-4"
      >
        <v-card-item>
          <v-card-title>割り当て</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!assignments.length"
            class="text-body-2 text-medium-emphasis"
          >
            割り当てはありません。
          </div>
          <v-table
            v-else
            density="compact"
          >
            <thead>
              <tr>
                <th>相手</th>
                <th>有効期間</th>
                <th class="text-no-wrap">
                  製造番号
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="assignment in assignments"
                :key="assignment.assignment_id"
                :data-test="`assignment-${assignment.assignment_id}`"
                :class="{ 'text-disabled': isAssignmentEnded(assignment) }"
              >
                <td v-if="assignment.target_type === 'group'">
                  <RouterLink
                    v-if="assignment.group_id"
                    :data-test="`assignment-group-${assignment.assignment_id}`"
                    :to="{ name: 'admin-users.group', params: { id: assignment.group_id } }"
                  >
                    {{ targetLabel(assignment) }}
                  </RouterLink>
                </td>
                <td v-else>
                  <RouterLink
                    v-if="assignment.user_id"
                    :data-test="`assignment-user-${assignment.assignment_id}`"
                    :to="{ name: 'admin-users.user', params: { userId: assignment.user_id } }"
                  >
                    {{ userLabel(assignment.user_id) }}
                  </RouterLink>
                  <v-chip
                    v-if="assignment.user_id === selfUserId"
                    size="x-small"
                    class="ms-2"
                  >
                    自分
                  </v-chip>
                </td>
                <td>{{ rangeLabel(assignment.valid_from, assignment.valid_to) }}</td>
                <td class="text-no-wrap">
                  {{ assignment.serial ?? DASH }}
                </td>
                <td class="text-end">
                  <v-btn
                    v-if="active && !isAssignmentEnded(assignment)"
                    :data-test="`end-assignment-${assignment.assignment_id}`"
                    variant="text"
                    color="warning"
                    size="small"
                    :disabled="busy"
                    @click="askEndAssignment(assignment)"
                  >
                    終える
                  </v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>

      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title>サービスの許可</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!grants.length"
            class="text-body-2 text-medium-emphasis"
          >
            許可はありません。
          </div>
          <v-table
            v-else
            density="compact"
          >
            <thead>
              <tr>
                <th>サービス</th>
                <th>期間</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="grantRow in grants"
                :key="grantRow.grant_id"
                :data-test="`grant-${grantRow.grant_id}`"
                :class="{ 'text-disabled': isGrantEnded(grantRow) }"
              >
                <td>{{ serviceName(grantRow.service) }}</td>
                <td>{{ rangeLabel(grantRow.starts_at, grantRow.ends_at) }}</td>
                <td class="text-end">
                  <v-btn
                    v-if="active && !isGrantEnded(grantRow)"
                    :data-test="`end-grant-${grantRow.grant_id}`"
                    variant="text"
                    color="warning"
                    size="small"
                    :disabled="busy"
                    @click="askEndGrant(grantRow)"
                  >
                    終える
                  </v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>
      <p class="text-caption text-medium-emphasis">
        終わりの時刻は期間に含みません。終えた割り当てと許可は消さずに薄く表示します。製造番号は初めての通信機ログインで記録します。
      </p>

      <AssignDialog
        :model-value="dialog === 'assign'"
        :started-at="target?.startedAt ?? period.started_at"
        :account="target?.account"
        :org-name="target?.orgName"
        :users="usersStore.users"
        :groups="groupsStore.groups"
        :self-user-id="selfUserId"
        :loading="busy"
        :error="dialogError"
        @update:model-value="closeDialog"
        @submit="assign"
      />
      <AccessDialog
        :model-value="dialog === 'access'"
        :access="period.access ?? 'everyone'"
        :changed-at="period.access_changed_at"
        :changed-by="period.access_changed_by ? userLabel(period.access_changed_by) : null"
        :loading="busy"
        :error="dialogError"
        @update:model-value="closeDialog"
        @submit="changeAccess"
      />
      <GrantDialog
        :model-value="dialog === 'grant'"
        :started-at="target?.startedAt ?? period.started_at"
        :account="target?.account"
        :org-name="target?.orgName"
        :loading="busy"
        :error="dialogError"
        @update:model-value="closeDialog"
        @submit="grant"
      />
      <EndConfirmDialog
        :model-value="dialog === 'end'"
        :account="target?.account ?? ''"
        :org-name="target?.orgName ?? ''"
        :loading="busy"
        @update:model-value="closeDialog"
        @confirm="endPeriod"
      />
    </template>

    <ConfirmDialog
      v-if="confirmAction"
      :model-value="dialog === 'confirm'"
      :title="confirmAction.title"
      :message="confirmAction.message"
      :warning="confirmAction.warning"
      :confirm-label="confirmAction.confirmLabel"
      confirm-color="warning"
      :loading="busy"
      @update:model-value="closeDialog"
      @confirm="confirm"
    />
  </div>
</template>
