<script setup lang="ts">
import { computed } from 'vue'
import type { Me } from '@/stores/me'
import { usePasskeysStore } from '@/stores/passkeys'

const props = defineProps<{
  me: Me | null
  error: string | null
  denied: string | null
}>()

const emit = defineEmits<{ retry: [] }>()
const passkeys = usePasskeysStore()

/** API に断られた理由 (計画 2.6 の 403) の案内。状態の帯と重なるものは出さない */
const DENIED_MESSAGES: Record<string, string> = {
  ADMIN_SESSION_STALE:
    '管理者の操作には、認証アプリの登録の後にパスワードと認証アプリのコードでサインインし直す必要があります。サインアウトしてから入り直してください。',
  ADMIN_MFA_MISSING:
    '認証アプリ (TOTP) の登録が見つかりません。管理者の操作はできません。別の管理者へ連絡してください。',
  ADMIN_NOT_ACTIVE: '管理者として有効ではないため、管理者の操作はできません。',
}

const adminState = computed(() => (props.me?.is_admin ? props.me.admin_state : null))
const suspended = computed(() => props.me?.status === 'suspended')
const suspendedMessage = computed(() => {
  if (props.me?.restore_requires !== 'reinvite') {
    return 'このアカウントは停止中です。サービスは使えません。再招待を受けた方は、この画面でパスキーを登録してから管理者へお知らせください。'
  }
  if (props.me.is_admin) {
    return '再招待の後のため、停止中です。認証アプリ (TOTP) の登録と、別の管理者 (または運用者) の確認を終えると有効になります。'
  }
  if (!passkeys.loaded) {
    return '再招待の後の確認待ちです。'
  }
  return passkeys.items.length > 0
    ? 'パスキーは登録済みです。管理者が有効にするまでお待ちください。'
    : '再招待の後の確認待ちです。下の「この端末でパスキーを登録する」でパスキーを登録してください。登録を確かめた後、管理者が有効にします。'
})
const deniedMessage = computed(() => (props.denied ? DENIED_MESSAGES[props.denied] : undefined))
</script>

<template>
  <div>
    <v-alert
      v-if="!me && error"
      data-test="status-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ error }}
      <div class="mt-2">
        <v-btn
          data-test="status-retry"
          variant="tonal"
          size="small"
          prepend-icon="mdi-refresh"
          @click="emit('retry')"
        >
          読み直す
        </v-btn>
      </div>
    </v-alert>

    <v-alert
      v-if="suspended"
      data-test="status-suspended"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      {{ suspendedMessage }}
    </v-alert>

    <v-alert
      v-if="adminState === 'pending'"
      data-test="status-pending"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      管理者の準備中です。下で認証アプリ (TOTP) を登録してください。登録を終えるまで管理者の操作はできません。
    </v-alert>

    <v-alert
      v-if="adminState === 'awaiting'"
      data-test="status-awaiting"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      確認待ちです。別の管理者 (または運用者) が社内の別経路であなたの登録を確かめて有効にするまでお待ちください。有効になったら、パスワードと認証アプリのコードでサインインし直してください。
    </v-alert>

    <v-alert
      v-if="deniedMessage && !suspended && adminState !== 'pending' && adminState !== 'awaiting'"
      data-test="status-denied"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      {{ deniedMessage }}
    </v-alert>
  </div>
</template>
