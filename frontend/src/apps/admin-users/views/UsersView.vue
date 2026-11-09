<script setup lang="ts">
import { computed, onActivated, ref } from 'vue'
import { useUiStore } from '@/stores/ui'
import { DASH } from '@/utils/format'
import InviteDialog from '../components/InviteDialog.vue'
import OneTimeSecretDialog from '../components/OneTimeSecretDialog.vue'
import {
  ADMIN_STATE_LABELS,
  COGNITO_STATUS_LABELS,
  KIND_LABELS,
  STATUS_LABELS,
  formatJst,
} from '../labels'
import { useUsersStore } from '../stores/users'
import type { InviteInput, UserSummary } from '../types'

const store = useUsersStore()
const ui = useUiStore()

const inviteOpen = ref(false)
const inviting = ref(false)
const inviteError = ref<string | null>(null)
// 一時パスワードはこの画面のメモリだけに置き、閉じたら捨てる
const secret = ref('')
const secretEmail = ref<string | null>(null)
const secretOpen = ref(false)

const isEmpty = computed(() => !store.loading && !store.error && store.users.length === 0)

function rowKey(user: UserSummary): string {
  return user.user_id ?? `unregistered-${user.email ?? ''}`
}

function isSelf(user: UserSummary): boolean {
  return user.user_id !== null && user.user_id === store.selfUserId
}

function roleLabel(user: UserSummary): string {
  if (user.role !== 'admin') {
    return user.role === 'user' ? '一般' : DASH
  }
  return user.admin_state ? `admin (${ADMIN_STATE_LABELS[user.admin_state]})` : 'admin'
}

function openInvite(): void {
  inviteError.value = null
  inviteOpen.value = true
}

async function invite(input: InviteInput): Promise<void> {
  inviting.value = true
  inviteError.value = null
  const result = await store.invite(input)
  inviting.value = false
  if (!result.ok) {
    inviteError.value = result.message
    return
  }
  inviteOpen.value = false
  ui.notify(`${result.data.email} を招待しました`, 'success')
  if (result.data.temporary_password) {
    secret.value = result.data.temporary_password
    secretEmail.value = result.data.email
    secretOpen.value = true
  }
}

function closeSecret(value: boolean): void {
  secretOpen.value = value
  if (!value) {
    secret.value = ''
    secretEmail.value = null
  }
}

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに取り直す（UI-04）
onActivated(() => store.fetch())
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <h1 class="text-h5 mb-1">
          利用者の管理
        </h1>
        <p class="text-body-2 text-medium-emphasis mb-0">
          台帳と Cognito の状態を並べて出します。操作は利用者の詳細の画面で行います。
        </p>
      </div>
      <v-spacer />
      <v-btn
        data-test="refresh"
        variant="text"
        prepend-icon="mdi-refresh"
        :loading="store.loading"
        @click="store.fetch()"
      >
        更新
      </v-btn>
      <v-btn
        data-test="invite-open"
        color="primary"
        prepend-icon="mdi-account-plus-outline"
        @click="openInvite"
      >
        招待する
      </v-btn>
    </div>

    <v-alert
      v-if="store.error"
      data-test="error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>

    <v-alert
      v-if="isEmpty"
      data-test="empty"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      利用者がいません。
    </v-alert>

    <v-card
      variant="flat"
      border
    >
      <v-progress-linear
        :active="store.loading"
        indeterminate
        color="primary"
      />
      <v-table density="comfortable">
        <thead>
          <tr>
            <th>メールアドレス</th>
            <th class="text-no-wrap">
              種別
            </th>
            <th>組織</th>
            <th class="text-no-wrap">
              状態
            </th>
            <th class="text-no-wrap">
              役割
            </th>
            <th class="text-no-wrap d-none d-lg-table-cell">
              登録日時
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="user in store.users"
            :key="rowKey(user)"
            :data-test="`row-${rowKey(user)}`"
          >
            <td class="text-no-wrap">
              <RouterLink
                v-if="user.user_id"
                :to="{ name: 'admin-users.user', params: { userId: user.user_id } }"
              >
                {{ user.email ?? DASH }}
              </RouterLink>
              <span v-else>{{ user.email ?? DASH }}</span>
              <v-chip
                v-if="isSelf(user)"
                data-test="self-chip"
                size="x-small"
                class="ms-2"
              >
                自分
              </v-chip>
              <v-chip
                v-if="!user.in_ledger"
                data-test="unregistered-chip"
                color="error"
                size="x-small"
                class="ms-2"
              >
                台帳なし
              </v-chip>
            </td>
            <td class="text-no-wrap">
              {{ user.kind ? KIND_LABELS[user.kind] : DASH }}
            </td>
            <td>{{ user.org_name ?? DASH }}</td>
            <td class="text-no-wrap">
              <v-chip
                :color="user.status === 'active' ? 'success' : 'grey'"
                size="small"
              >
                {{ user.status ? STATUS_LABELS[user.status] : DASH }}
              </v-chip>
              <div class="text-caption text-medium-emphasis">
                Cognito: {{ user.cognito_enabled ? '有効' : '無効' }}
                <template v-if="user.cognito_status">
                  ・{{ COGNITO_STATUS_LABELS[user.cognito_status] ?? user.cognito_status }}
                </template>
              </div>
            </td>
            <td class="text-no-wrap">
              {{ roleLabel(user) }}
            </td>
            <td class="text-no-wrap d-none d-lg-table-cell">
              {{ formatJst(user.created_at) }}
            </td>
          </tr>
        </tbody>
      </v-table>
      <div class="text-caption text-medium-emphasis px-4 py-2">
        「台帳なし」は Cognito にだけいる利用者です (招待・再招待の途中の失敗など)。操作は監査テーブルと CloudWatch Logs に残ります。
      </div>
    </v-card>

    <InviteDialog
      v-model="inviteOpen"
      :loading="inviting"
      :error="inviteError"
      @submit="invite"
    />
    <OneTimeSecretDialog
      :model-value="secretOpen"
      :secret="secret"
      :email="secretEmail"
      @update:model-value="closeSecret"
    />
  </div>
</template>
