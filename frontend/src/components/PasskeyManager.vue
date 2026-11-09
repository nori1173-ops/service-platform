<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { baseApi } from '@/services/baseApi'
import { extractApiError } from '@/services/apiError'
import { PASSKEY_LIMIT, usePasskeysStore, type PasskeyAttachment, type PasskeyItem } from '@/stores/passkeys'
import { useUiStore } from '@/stores/ui'
import { DASH, formatDateTime } from '@/utils/format'

const props = defineProps<{ admin?: boolean }>()

const ATTACHMENT_LABELS: Record<PasskeyAttachment, string> = {
  platform: 'この端末に組み込み (指紋・顔・PIN など)',
  'cross-platform': '別の機器 (スマホ・セキュリティキーなど)',
}

const passkeys = usePasskeysStore()
const ui = useUiStore()

const MFA_PENDING_KEY = 'service-platform.admin-passkey-mfa-pending'
const enabling = ref(false)
const passkeyRequired = ref(false)
const mfaError = ref<string | null>(null)
const dialog = ref(false)
const target = ref<PasskeyItem | null>(null)

function attachmentLabel(item: PasskeyItem): string {
  return item.attachment ? ATTACHMENT_LABELS[item.attachment] : DASH
}

function hasPendingMfa(): boolean {
  try {
    return sessionStorage.getItem(MFA_PENDING_KEY) === '1'
  } catch {
    return false
  }
}

function setPendingMfa(pending: boolean): void {
  try {
    if (pending) {
      sessionStorage.setItem(MFA_PENDING_KEY, '1')
    } else {
      sessionStorage.removeItem(MFA_PENDING_KEY)
    }
  } catch {
    // 印を保存できなくても利用設定と画面の通知は続ける。
  }
}

async function enableAdminPasskey(): Promise<boolean> {
  enabling.value = true
  mfaError.value = null
  passkeyRequired.value = false
  try {
    await baseApi.enablePasskeyMfa()
    setPendingMfa(false)
    return true
  } catch (error) {
    passkeyRequired.value = extractApiError(error)?.code === 'PASSKEY_REQUIRED'
      && (error as { response?: { status?: number } })?.response?.status === 409
    setPendingMfa(!passkeyRequired.value)
    mfaError.value = passkeyRequired.value
      ? '先にパスキーを登録してください'
      : 'パスキーの利用設定を完了できませんでした。「利用設定をやり直す」を押してください。パスワードと認証アプリでも入れます。'
    return false
  } finally {
    enabling.value = false
  }
}

async function add(): Promise<void> {
  if (await passkeys.add()) {
    if (props.admin && !await enableAdminPasskey()) return
    ui.notify('パスキーを登録しました。次からこのパスキーでサインインできます', 'success')
  }
}

function askRemove(item: PasskeyItem): void {
  target.value = item
  dialog.value = true
}

async function remove(): Promise<void> {
  const item = target.value
  if (!item) {
    return
  }
  const removed = await passkeys.remove(item.credentialId)
  dialog.value = false
  target.value = null
  if (removed) {
    ui.notify('パスキーを削除しました', 'success')
  }
}

onMounted(async () => {
  await passkeys.fetch()
  if (props.admin && hasPendingMfa()) {
    await enableAdminPasskey()
  }
})
</script>

<template>
  <v-card
    variant="flat"
    border
  >
    <v-card-item>
      <v-card-title>パスキー</v-card-title>
      <v-card-subtitle>サインインに使うパスキーの一覧です。</v-card-subtitle>
    </v-card-item>
    <v-card-text>
      <v-alert
        v-if="passkeys.error"
        data-test="passkey-error"
        type="error"
        variant="tonal"
        class="mb-4"
      >
        {{ passkeys.error }}
      </v-alert>

      <v-alert
        v-if="mfaError"
        data-test="passkey-mfa-error"
        type="error"
        variant="tonal"
        class="mb-4"
      >
        {{ mfaError }}
        <v-btn
          v-if="!passkeyRequired"
          data-test="passkey-enable"
          class="mt-2"
          variant="tonal"
          :loading="enabling"
          @click="enableAdminPasskey"
        >
          利用設定をやり直す
        </v-btn>
      </v-alert>

      <v-alert
        v-if="passkeys.loaded && passkeys.items.length === 0"
        data-test="passkey-empty"
        type="info"
        variant="tonal"
        class="mb-4"
      >
        パスキーがまだありません。登録すると、次から{{ props.admin ? '' : 'メールのコードを待たずに、' }}この端末の指紋・顔の認証や PIN でサインインできます。
      </v-alert>

      <v-table
        v-if="passkeys.items.length"
        density="comfortable"
        class="mb-4"
      >
        <thead>
          <tr>
            <th>名前</th>
            <th class="text-no-wrap">
              登録日時
            </th>
            <th>認証器の種類</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in passkeys.items"
            :key="item.credentialId"
            :data-test="`passkey-row-${item.credentialId}`"
          >
            <td>{{ item.name ?? DASH }}</td>
            <td class="text-no-wrap">
              {{ item.createdAt ? formatDateTime(item.createdAt) : DASH }}
            </td>
            <td>{{ attachmentLabel(item) }}</td>
            <td class="text-end">
              <v-btn
                :data-test="`passkey-delete-${item.credentialId}`"
                icon="mdi-delete-outline"
                variant="text"
                size="small"
                color="error"
                title="削除"
                :disabled="passkeys.loading || enabling"
                @click="askRemove(item)"
              />
            </td>
          </tr>
        </tbody>
      </v-table>

      <v-btn
        data-test="passkey-add"
        color="primary"
        prepend-icon="mdi-fingerprint"
        :loading="passkeys.loading || enabling"
        :disabled="passkeys.atLimit || enabling"
        @click="add"
      >
        この端末でパスキーを登録する
      </v-btn>
      <div
        v-if="passkeys.atLimit"
        data-test="passkey-limit"
        class="text-caption text-warning mt-2"
      >
        パスキーは {{ PASSKEY_LIMIT }} 個までです。使わなくなったものを削除してから登録してください。
      </div>

      <p
        data-test="passkey-advice"
        class="text-caption text-medium-emphasis mt-4 mb-0"
      >
        端末ごと (PC とスマホなど) に 1 つずつ登録しておくと、1 台を失くしてももう 1 台でサインインできます。別の端末で登録するときは、その端末で{{ props.admin ? 'パスワードと認証アプリ' : 'メールのコード' }}でサインインしてから、この画面で登録してください。1 人 {{ PASSKEY_LIMIT }} 個まで登録できます。名前は自動で付き、変えられません。
      </p>
    </v-card-text>

    <ConfirmDialog
      v-model="dialog"
      title="パスキーを削除しますか?"
      :message="`${target?.name ?? 'このパスキー'} (登録 ${target?.createdAt ? formatDateTime(target.createdAt) : DASH}) を削除します。`"
      :warning="`削除したパスキーではサインインできなくなります。この端末のパスキーを消すと、ほかのパスキーか${props.admin ? 'パスワードと認証アプリ' : 'メールのコード'}でサインインすることになります。`"
      confirm-label="削除する"
      confirm-color="error"
      :loading="passkeys.loading"
      @confirm="remove"
    />
  </v-card>
</template>
