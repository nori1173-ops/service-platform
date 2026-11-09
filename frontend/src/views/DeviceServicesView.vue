<script setup lang="ts">
import { computed, onActivated, ref, watch } from 'vue'
import { extractApiError } from '@/services/apiError'
import { baseApi } from '@/services/baseApi'
import { MESSAGES } from '@/stores/device'
import type { DeviceServices } from '@/types/devices'
import { DASH, formatJst } from '@/utils/format'
import { leaveTo } from '@/utils/navigation'

const props = defineProps<{ account: string }>()

const SERVICE_DESCRIPTIONS: Readonly<Record<string, string>> = {
  camera: 'カメラの画像を AI で判定し、条件に合ったときに通知します。',
}

/** 通信機ログインからやり直す理由 */
const RETRY_LOGIN: Readonly<Record<string, string>> = {
  DEVICE_LOGIN_REQUIRED: MESSAGES.loginRequired,
  DEVICE_NOT_ASSIGNED: MESSAGES.notAssigned,
}

const data = ref<DeviceServices | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const needsLogin = ref(false)
let request = 0

const methodLabel = computed(() => {
  const method = data.value?.method
  return method === 'latest' ? '最新値' : method === 'previous' ? '前回値' : DASH
})

async function load(): Promise<void> {
  const current = ++request
  loading.value = true
  error.value = null
  needsLogin.value = false
  try {
    const result = await baseApi.getDeviceServices(props.account)
    if (current === request) {
      data.value = result
    }
  } catch (e: unknown) {
    if (current !== request) {
      return
    }
    data.value = null
    const api = extractApiError(e)
    const retry = api?.code ? RETRY_LOGIN[api.code] : undefined
    needsLogin.value = retry !== undefined
    error.value = retry ?? api?.message ?? 'サービスの一覧を読めませんでした。しばらくしてから読み直してください。'
  } finally {
    if (current === request) {
      loading.value = false
    }
  }
}

// 同じタブで開く (ADR D6)。サービスの API は台帳で通信機の条件を確かめ直す。
// 起動先は同じサイトの絶対パスだけ (API の値でも外のサイトへは移らない)
function launch(url: string): void {
  if (!url.startsWith('/') || url.startsWith('//')) {
    return
  }
  leaveTo(url)
}

watch(
  () => props.account,
  () => void load(),
  { immediate: true },
)

// keep-alive で戻ったときも読み直す (許可の追加・終了、ログインのやり直しや期限切れを映す)。
// 最初の表示では watch が読むので、2 回目の表示から読む
let first = true
onActivated(() => {
  if (first) {
    first = false
    return
  }
  void load()
})
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      サービスの一覧
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      この通信機で使えるサービスです。
    </p>

    <v-progress-linear
      v-if="loading"
      indeterminate
      color="primary"
      class="mb-4"
    />

    <v-alert
      v-if="error"
      data-test="services-error"
      :type="needsLogin ? 'warning' : 'error'"
      variant="tonal"
      class="mb-4"
    >
      {{ error }}
      <div class="d-flex flex-wrap ga-2 mt-3">
        <v-btn
          v-if="needsLogin"
          data-test="back-to-login"
          :to="{ name: 'devices' }"
          color="primary"
          variant="flat"
        >
          通信機ログインへ
        </v-btn>
        <v-btn
          v-else
          data-test="services-retry"
          variant="tonal"
          @click="load()"
        >
          読み直す
        </v-btn>
      </div>
    </v-alert>

    <template v-if="data">
      <v-card
        variant="flat"
        border
        class="mb-4"
        data-test="device-summary"
      >
        <v-card-text>
          <v-table density="compact">
            <tbody>
              <tr>
                <th class="text-no-wrap">
                  通信機アカウント
                </th>
                <td>{{ data.account }}</td>
              </tr>
              <tr>
                <th class="text-no-wrap">
                  組織
                </th>
                <td>{{ data.org_name ?? DASH }}</td>
              </tr>
              <tr>
                <th class="text-no-wrap">
                  現場
                </th>
                <td>{{ data.location_name ?? DASH }}</td>
              </tr>
              <tr>
                <th class="text-no-wrap">
                  通信機ログイン
                </th>
                <td>{{ methodLabel }} (通信機設定)・{{ formatJst(data.expires_at) }} まで有効</td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>

      <v-alert
        v-if="data.services.length === 0"
        data-test="no-services"
        type="info"
        variant="tonal"
      >
        この通信機で使えるサービスはありません。サービスの許可は管理者へお問い合わせください。
      </v-alert>

      <v-row v-else>
        <v-col
          v-for="service in data.services"
          :key="service.id"
          cols="12"
          sm="6"
          md="4"
        >
          <v-card
            :data-test="`service-${service.id}`"
            height="100%"
            border
            flat
          >
            <v-card-item>
              <template #prepend>
                <v-icon
                  icon="mdi-cctv"
                  color="primary"
                  size="32"
                />
              </template>
              <v-card-title>{{ service.name }}</v-card-title>
              <v-card-subtitle v-if="service.ends_at">
                {{ formatJst(service.ends_at) }} まで
              </v-card-subtitle>
            </v-card-item>
            <v-card-text class="text-body-2">
              {{ SERVICE_DESCRIPTIONS[service.id] ?? '' }}
            </v-card-text>
            <v-card-actions>
              <v-btn
                :data-test="`launch-${service.id}`"
                color="primary"
                variant="flat"
                @click="launch(service.url)"
              >
                開く
              </v-btn>
            </v-card-actions>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </div>
</template>
