<script setup lang="ts">
import { computed, onActivated, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useMeStore } from '@/stores/me'
import { useUiStore } from '@/stores/ui'
import { DASH } from '@/utils/format'
import EmailDialog from '../components/EmailDialog.vue'
import OneTimeSecretDialog from '../components/OneTimeSecretDialog.vue'
import ProfileDialog from '../components/ProfileDialog.vue'
import ReinviteDialog from '../components/ReinviteDialog.vue'
import SuspendDialog from '../components/SuspendDialog.vue'
import VerifyDialog from '../components/VerifyDialog.vue'
import {
  ADMIN_STATE_LABELS,
  COGNITO_STATUS_LABELS,
  KIND_LABELS,
  STATUS_LABELS,
  VERIFY_METHOD_LABELS,
  formatJst,
} from '../labels'
import { isEnded } from '../periodLabels'
import { useUsersStore, type OpResult } from '../stores/users'
import type { AuthFactors, ProfileInput, ReinviteInput, VerifyPayload } from '../types'

const props = defineProps<{ userId: string }>()

const SELF_REASON = '自分自身には操作できません'
const TARGET_CHANGED = '表示している利用者が変わったため、操作を取りやめました。詳細を確かめてからやり直してください'

const FACTORS: { key: keyof AuthFactors; label: string }[] = [
  { key: 'web_authn', label: 'パスキー' },
  { key: 'email_otp', label: 'メールのコード' },
  { key: 'password', label: 'パスワード' },
]

type Dialog = 'suspend' | 'restore' | 'activate' | 'reinvite' | 'email' | 'profile' | 'confirm' | null

/** 確認を開いた時点の対象。実行はこの利用者に送る (後から届いた別の詳細で入れ替わらない) */
interface Target {
  userId: string
  email: string | null
}

interface PendingConfirm {
  title: string
  message: string
  warning?: string
  confirmLabel: string
  confirmColor: string
  run: () => Promise<void>
}

const store = useUsersStore()
const meStore = useMeStore()
const ui = useUiStore()

const dialog = ref<Dialog>(null)
const working = ref(false)
const confirmAction = ref<PendingConfirm | null>(null)
const target = ref<Target | null>(null)
// 一時パスワードはこの画面のメモリだけに置き、閉じたら捨てる
const secret = ref('')
const secretOpen = ref(false)

// URL の利用者の詳細だけを出す。別の利用者へ移る間は前の利用者の詳細と操作を出さない
const user = computed(() => (store.detail?.user_id === props.userId ? store.detail : null))
// 一覧を読まずに直接 URL で開いても判定できるよう、自分の user_id は me から取る
const selfUserId = computed(() => meStore.me?.user_id ?? store.selfUserId)
const isSelf = computed(() => !!user.value && user.value.user_id === selfUserId.value)
const busy = computed(() => store.detailLoading || working.value)
const adminTarget = computed(
  () => !!user.value && (user.value.role === 'admin' || user.value.is_admin || !!user.value.admin_state),
)
const canSuspend = computed(() => !!user.value && (user.value.status === 'active' || user.value.cognito_enabled))
const canRestore = computed(() => {
  const current = user.value
  if (!current) {
    return false
  }
  if (current.status === 'active') {
    return !current.cognito_enabled
  }
  return !(adminTarget.value && current.restore_requires === 'reinvite')
})
const canActivate = computed(() => user.value?.role === 'admin' && user.value.admin_state === 'awaiting')
// お客様と社員はパスワードを持たない (ADR D1)。あれば後から足された疑い
const passwordAdded = computed(() => !adminTarget.value && !!user.value?.factors?.password)

function open(name: Dialog): (value: boolean) => void {
  return (value) => {
    dialog.value = value ? name : null
    if (!value) {
      target.value = null
    }
  }
}

function openDialog(name: Exclude<Dialog, null>): void {
  const current = user.value
  if (!current?.user_id || busy.value) {
    return
  }
  target.value = { userId: current.user_id, email: current.email }
  dialog.value = name
}

function closeAll(): void {
  dialog.value = null
  target.value = null
  confirmAction.value = null
}

function load(): void {
  void store.fetchDetail(props.userId)
}

onActivated(load)
watch(
  () => props.userId,
  () => {
    closeAll()
    load()
  },
)

function factorLabel(key: keyof AuthFactors): string {
  const factors = user.value?.factors
  if (!factors) {
    return DASH
  }
  return factors[key] ? 'あり' : 'なし'
}

function showSecret(value: string | undefined): void {
  if (value) {
    secret.value = value
    secretOpen.value = true
  }
}

function closeSecret(value: boolean): void {
  secretOpen.value = value
  if (!value) {
    secret.value = ''
  }
}

/** 確認を開いた時点の利用者に送る。その後に URL の利用者が変わっていたら実行しない */
async function run<T>(
  action: (userId: string) => Promise<OpResult<T>>,
  success: string,
): Promise<OpResult<T> | null> {
  const current = target.value
  if (!current || current.userId !== props.userId) {
    closeAll()
    ui.notify(TARGET_CHANGED, 'error')
    return null
  }
  working.value = true
  const result = await action(current.userId)
  working.value = false
  dialog.value = null
  target.value = null
  ui.notify(result.ok ? success : result.message, result.ok ? 'success' : 'error')
  return result
}

function suspend(reason: string | null): void {
  void run((userId) => store.suspend(userId, reason), '停止しました')
}

function restore(payload: VerifyPayload): void {
  const input = payload.note
    ? { verified_method: payload.method, note: payload.note }
    : { verified_method: payload.method }
  void run((userId) => store.restore(userId, input), '有効に戻しました')
}

function activate(payload: VerifyPayload): void {
  void run((userId) => store.activateAdmin(userId, payload), 'admin を有効にしました')
}

async function reinvite(input: ReinviteInput): Promise<void> {
  const result = await run((userId) => store.reinvite(userId, input), '再招待しました')
  if (result?.ok) {
    showSecret(result.data.temporary_password)
  }
}

function changeEmail(email: string): void {
  void run((userId) => store.changeEmail(userId, email), 'メールアドレスを変えました')
}

function updateProfile(input: ProfileInput): void {
  void run((userId) => store.updateProfile(userId, input), '台帳の項目を保存しました')
}

function askConfirm(action: PendingConfirm): void {
  if (!user.value?.user_id || busy.value) {
    return
  }
  confirmAction.value = action
  openDialog('confirm')
}

function askTemporaryPassword(): void {
  askConfirm({
    title: '一時パスワードを発行しますか?',
    message: `${user.value?.email ?? ''} の一時パスワードを設定します。本人は次のサインインで新しいパスワードを決めます。`,
    warning: '今のパスワードは使えなくなります。一時パスワードは 1 回だけ表示します。',
    confirmLabel: '発行する',
    confirmColor: 'primary',
    run: async () => {
      const result = await run((userId) => store.issueTemporaryPassword(userId), '一時パスワードを発行しました')
      if (result?.ok) {
        showSecret(result.data.temporary_password)
      }
    },
  })
}

function askRevokeAdmin(): void {
  askConfirm({
    title: 'admin を外しますか?',
    message: `${user.value?.email ?? ''} を admin グループから外し、台帳の役割を一般に戻します。`,
    warning: 'パスワードと TOTP は Cognito に残ります。認証要素も消すには user として再招待してください。admin に戻すには、再招待で役割に admin を選びます。',
    confirmLabel: '外す',
    confirmColor: 'warning',
    run: async () => {
      await run((userId) => store.revokeAdmin(userId), 'admin を外しました')
    },
  })
}

async function confirm(): Promise<void> {
  const action = confirmAction.value
  confirmAction.value = null
  if (action) {
    await action.run()
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
        :to="{ name: 'admin-users.users' }"
      >
        利用者の一覧へ
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
      v-if="!user && store.detailLoading"
      data-test="detail-loading"
      type="heading, table-tbody"
      class="mb-4"
    />

    <template v-if="user">
      <div class="d-flex align-center flex-wrap ga-2 mb-4">
        <h1 class="text-h5 mb-0">
          {{ user.email ?? DASH }}
        </h1>
        <v-chip
          :color="user.status === 'active' ? 'success' : 'grey'"
          size="small"
        >
          {{ user.status ? STATUS_LABELS[user.status] : DASH }}
        </v-chip>
        <v-chip
          v-if="adminTarget"
          color="primary"
          size="small"
        >
          admin{{ user.admin_state ? ` (${ADMIN_STATE_LABELS[user.admin_state]})` : '' }}
        </v-chip>
        <v-chip
          v-if="isSelf"
          size="small"
        >
          自分
        </v-chip>
      </div>

      <v-alert
        v-if="user.reinvite_in_progress"
        data-test="reinvite-in-progress"
        type="warning"
        variant="tonal"
        class="mb-4"
      >
        再招待が終わっていません。もう一度「再招待」を行うと続きから進みます。
      </v-alert>
      <v-alert
        v-if="user.email_pending"
        data-test="email-pending"
        type="warning"
        variant="tonal"
        class="mb-4"
      >
        メールアドレスの変更 ({{ user.email_pending }} へ) が途中です。同じ宛先でもう一度変えると続きから進みます。
      </v-alert>
      <v-alert
        v-if="passwordAdded"
        data-test="password-warning"
        type="warning"
        variant="tonal"
        class="mb-4"
      >
        この利用者はパスワードでもサインインできます (パスワードが後から足された疑い)。乗っ取りが疑われるときは再招待してください。
      </v-alert>

      <v-row>
        <v-col
          cols="12"
          md="6"
        >
          <v-card
            variant="flat"
            border
            class="mb-4"
          >
            <v-card-item>
              <v-card-title>台帳</v-card-title>
            </v-card-item>
            <v-table density="compact">
              <tbody>
                <tr>
                  <th>種別</th>
                  <td>{{ user.kind ? KIND_LABELS[user.kind] : DASH }}</td>
                </tr>
                <tr>
                  <th>組織</th>
                  <td>{{ user.org_name ?? DASH }}</td>
                </tr>
                <tr>
                  <th>連絡先</th>
                  <td>{{ user.contact ?? DASH }}</td>
                </tr>
                <tr>
                  <th>メモ</th>
                  <td class="text-pre-wrap">
                    {{ user.note ?? DASH }}
                  </td>
                </tr>
                <tr>
                  <th>停止</th>
                  <td>
                    {{ user.suspended_at ? formatJst(user.suspended_at) : DASH }}
                    <span v-if="user.suspend_reason">({{ user.suspend_reason }})</span>
                    <span v-if="user.restore_requires === 'reinvite'"> 再招待の後</span>
                  </td>
                </tr>
                <tr>
                  <th>再招待の回数</th>
                  <td>{{ user.reinvite_count }}</td>
                </tr>
                <tr>
                  <th>登録 / 更新</th>
                  <td>{{ formatJst(user.created_at) }} / {{ formatJst(user.updated_at) }}</td>
                </tr>
                <tr>
                  <th>user_id</th>
                  <td class="text-caption">
                    {{ user.user_id }}
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-card>
        </v-col>
        <v-col
          cols="12"
          md="6"
        >
          <v-card
            variant="flat"
            border
            class="mb-4"
          >
            <v-card-item>
              <v-card-title>サインイン (Cognito)</v-card-title>
            </v-card-item>
            <v-table density="compact">
              <tbody>
                <tr>
                  <th>Cognito</th>
                  <td>
                    {{ user.cognito_enabled ? '有効' : '無効' }}
                    <template v-if="user.cognito_status">
                      ・{{ COGNITO_STATUS_LABELS[user.cognito_status] ?? user.cognito_status }}
                    </template>
                  </td>
                </tr>
                <tr
                  v-for="factor in FACTORS"
                  :key="factor.key"
                >
                  <th>{{ factor.label }}</th>
                  <td :data-test="`factor-${factor.key}`">
                    {{ factorLabel(factor.key) }}
                  </td>
                </tr>
                <tr>
                  <th>認証アプリ (TOTP)</th>
                  <td data-test="totp">
                    {{ user.totp ? 'あり' : 'なし' }}
                  </td>
                </tr>
                <tr v-if="adminTarget">
                  <th>TOTP の登録の時刻</th>
                  <td data-test="mfa-enabled-at">
                    {{ formatJst(user.mfa_enabled_at) }}
                  </td>
                </tr>
                <tr v-if="adminTarget">
                  <th>有効にした確認</th>
                  <td>
                    {{ formatJst(user.admin_verified_at) }}
                    <span v-if="user.admin_verified_method">({{ VERIFY_METHOD_LABELS[user.admin_verified_method] }})</span>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-card>
        </v-col>
      </v-row>

      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title>通信機の割り当て</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!user.assignments.length"
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
                <th>通信機アカウント</th>
                <th>組織</th>
                <th>有効期間</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="assignment in user.assignments"
                :key="assignment.assignment_id ?? ''"
                :data-test="`user-assignment-${assignment.assignment_id}`"
                :class="{ 'text-disabled': isEnded(assignment.valid_to) }"
              >
                <td>
                  <RouterLink
                    v-if="assignment.device_period_id"
                    :data-test="`user-period-${assignment.device_period_id}`"
                    :to="{ name: 'admin-users.device-period', params: { id: assignment.device_period_id } }"
                  >
                    {{ assignment.account ?? DASH }}
                  </RouterLink>
                  <span v-else>{{ assignment.account ?? DASH }}</span>
                </td>
                <td>{{ assignment.org_name ?? DASH }}</td>
                <td>
                  {{ formatJst(assignment.valid_from) }} 〜
                  {{ assignment.valid_to ? formatJst(assignment.valid_to) : '利用期間の終わりまで' }}
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
          <v-card-title>所属するグループ</v-card-title>
        </v-card-item>
        <v-card-text>
          <div
            v-if="!(user.memberships ?? []).length"
            class="text-body-2 text-medium-emphasis"
            data-test="user-memberships-empty"
          >
            グループに属していません。グループの画面で属させます。
          </div>
          <ul v-else>
            <li
              v-for="membership in user.memberships"
              :key="membership.membership_id"
              :data-test="`user-membership-${membership.group_id}`"
            >
              <RouterLink :to="{ name: 'admin-users.group', params: { id: membership.group_id } }">
                {{ membership.path_names.filter((name) => !!name).join(' > ') }}
              </RouterLink>
            </li>
          </ul>
        </v-card-text>
      </v-card>

      <v-card
        variant="flat"
        border
      >
        <v-card-item>
          <v-card-title>操作</v-card-title>
          <v-card-subtitle v-if="isSelf">
            {{ SELF_REASON }} (台帳の項目とメールアドレスを除く)
          </v-card-subtitle>
        </v-card-item>
        <v-card-text class="d-flex flex-wrap ga-2">
          <v-btn
            data-test="op-profile"
            variant="tonal"
            prepend-icon="mdi-pencil-outline"
            :disabled="busy"
            @click="openDialog('profile')"
          >
            台帳の項目を直す
          </v-btn>
          <v-btn
            data-test="op-email"
            variant="tonal"
            prepend-icon="mdi-email-edit-outline"
            :disabled="busy"
            @click="openDialog('email')"
          >
            メールアドレスを変える
          </v-btn>
          <v-btn
            v-if="canSuspend"
            data-test="op-suspend"
            variant="tonal"
            color="warning"
            prepend-icon="mdi-account-off-outline"
            :disabled="busy || isSelf"
            @click="openDialog('suspend')"
          >
            {{ user.status === 'suspended' ? '停止を流し直す' : '停止する' }}
          </v-btn>
          <v-btn
            v-if="canRestore"
            data-test="op-restore"
            variant="tonal"
            color="success"
            prepend-icon="mdi-account-check-outline"
            :disabled="busy || isSelf"
            @click="openDialog('restore')"
          >
            有効に戻す
          </v-btn>
          <v-btn
            data-test="op-reinvite"
            variant="tonal"
            color="warning"
            prepend-icon="mdi-account-convert-outline"
            :disabled="busy || isSelf"
            @click="openDialog('reinvite')"
          >
            再招待する
          </v-btn>
          <v-btn
            v-if="canActivate"
            data-test="op-activate-admin"
            variant="tonal"
            color="primary"
            prepend-icon="mdi-shield-check-outline"
            :disabled="busy || isSelf"
            @click="openDialog('activate')"
          >
            admin を有効にする
          </v-btn>
          <v-btn
            v-if="user.is_admin"
            data-test="op-temporary-password"
            variant="tonal"
            prepend-icon="mdi-form-textbox-password"
            :disabled="busy || isSelf"
            @click="askTemporaryPassword"
          >
            一時パスワードを発行する
          </v-btn>
          <v-btn
            v-if="adminTarget"
            data-test="op-revoke-admin"
            variant="tonal"
            color="warning"
            prepend-icon="mdi-shield-off-outline"
            :disabled="busy || isSelf"
            @click="askRevokeAdmin"
          >
            admin を外す
          </v-btn>
        </v-card-text>
      </v-card>

      <SuspendDialog
        :model-value="dialog === 'suspend'"
        :email="target?.email ?? null"
        :loading="working"
        @update:model-value="open('suspend')($event)"
        @submit="suspend"
      />
      <VerifyDialog
        :model-value="dialog === 'restore'"
        title="有効に戻しますか?"
        :message="`${target?.email ?? ''} を有効に戻します。別経路で確かめた方法とメモは台帳と監査に残ります。`"
        :warning="user.restore_requires === 'reinvite' ? '再招待の後は、本人が新しいパスキーを登録していることが要ります。メールでの申し出だけでは戻さないでください。' : ''"
        confirm-label="有効に戻す"
        :loading="working"
        @update:model-value="open('restore')($event)"
        @submit="restore"
      />
      <VerifyDialog
        :model-value="dialog === 'activate'"
        title="admin を有効にしますか?"
        :message="`${target?.email ?? ''} を admin として有効にします。TOTP の登録の時刻: ${formatJst(user.mfa_enabled_at)}`"
        warning="社内の別経路 (電話や対面) で本人に、この時刻に自分で登録したことを確かめてから有効にしてください。"
        confirm-label="有効にする"
        :loading="working"
        @update:model-value="open('activate')($event)"
        @submit="activate"
      />
      <ReinviteDialog
        :model-value="dialog === 'reinvite'"
        :email="target?.email ?? null"
        :is-admin="adminTarget"
        :kind="user.kind ?? 'customer'"
        :loading="working"
        @update:model-value="open('reinvite')($event)"
        @submit="reinvite"
      />
      <EmailDialog
        :model-value="dialog === 'email'"
        :current="target?.email ?? null"
        :loading="working"
        @update:model-value="open('email')($event)"
        @submit="changeEmail"
      />
      <ProfileDialog
        :model-value="dialog === 'profile'"
        :kind="user.kind"
        :org-name="user.org_name"
        :contact="user.contact"
        :note="user.note"
        :loading="working"
        @update:model-value="open('profile')($event)"
        @submit="updateProfile"
      />
    </template>

    <ConfirmDialog
      v-if="confirmAction"
      :model-value="dialog === 'confirm'"
      :title="confirmAction.title"
      :message="confirmAction.message"
      :warning="confirmAction.warning"
      :confirm-label="confirmAction.confirmLabel"
      :confirm-color="confirmAction.confirmColor"
      :loading="working"
      @update:model-value="open('confirm')($event)"
      @confirm="confirm"
    />
    <OneTimeSecretDialog
      :model-value="secretOpen"
      :secret="secret"
      :email="user?.email ?? null"
      @update:model-value="closeSecret"
    />
  </div>
</template>
