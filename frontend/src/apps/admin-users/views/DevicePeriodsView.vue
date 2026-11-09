<script setup lang="ts">
import { computed, onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { DASH } from '@/utils/format'
import StartDevicePeriodDialog from '../components/StartDevicePeriodDialog.vue'
import { formatJst } from '../labels'
import { ACCESS_LABELS, PERIOD_STATUS_LABELS, isAssignmentEnded, isGrantActive, serviceName } from '../periodLabels'
import { useDevicePeriodsStore } from '../stores/devicePeriods'
import type { DevicePeriodDetail, DevicePeriodFilter, StartDevicePeriodInput } from '../types'

const store = useDevicePeriodsStore()
const ui = useUiStore()
const router = useRouter()

const startOpen = ref(false)
const startError = ref<string | null>(null)

const busy = computed(() => store.loading || store.working)
const isEmpty = computed(() => !store.loading && !store.error && store.periods.length === 0)

function openAssignments(period: DevicePeriodDetail): number {
  return period.assignments.filter((assignment) => !isAssignmentEnded(assignment)).length
}

function activeServices(period: DevicePeriodDetail): string {
  const names = new Set(period.grants.filter((grant) => isGrantActive(grant)).map((grant) => serviceName(grant.service)))
  return names.size ? [...names].join('、') : DASH
}

function changeFilter(value: DevicePeriodFilter | null): void {
  if (value && value !== store.status) {
    void store.fetch(value)
  }
}

function openStart(): void {
  if (busy.value) {
    return
  }
  startError.value = null
  startOpen.value = true
}

async function start(input: StartDevicePeriodInput): Promise<void> {
  startError.value = null
  const result = await store.start(input)
  if (!result.ok) {
    startError.value = result.message
    return
  }
  startOpen.value = false
  ui.notify(`${result.data.account} の利用期間を始めました。続けて利用者を割り当て、サービスの許可を出してください`, 'success')
  await router.push({ name: 'admin-users.device-period', params: { id: result.data.device_period_id } })
}

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに取り直す（UI-04）
onActivated(() => store.fetch())
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <h1 class="text-h5 mb-1">
          通信機の利用期間
        </h1>
        <p class="text-body-2 text-medium-emphasis mb-0">
          通信機ごとの利用期間と、割り当て・サービスの許可の要約です。操作は利用期間の詳細の画面で行います。
        </p>
      </div>
      <v-spacer />
      <v-btn-toggle
        :model-value="store.status"
        mandatory
        density="compact"
        variant="outlined"
        divided
        @update:model-value="changeFilter"
      >
        <v-btn
          data-test="filter-active"
          value="active"
        >
          稼働中
        </v-btn>
        <v-btn
          data-test="filter-all"
          value="all"
        >
          全部
        </v-btn>
      </v-btn-toggle>
      <v-btn
        data-test="reload-periods"
        variant="text"
        prepend-icon="mdi-refresh"
        :loading="store.loading"
        @click="store.fetch()"
      >
        更新
      </v-btn>
      <v-btn
        data-test="start-period"
        color="primary"
        prepend-icon="mdi-plus"
        :disabled="busy"
        @click="openStart"
      >
        利用期間を始める
      </v-btn>
    </div>

    <v-alert
      v-if="store.error"
      data-test="periods-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>

    <v-alert
      v-if="isEmpty"
      data-test="periods-empty"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      {{ store.status === 'active' ? '稼働中の利用期間はありません。' : '利用期間はありません。' }}
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
            <th class="text-no-wrap">
              通信機アカウント
            </th>
            <th>組織名</th>
            <th class="text-no-wrap">
              開始
            </th>
            <th class="text-no-wrap">
              状態
            </th>
            <th class="text-no-wrap">
              通信機ログイン
            </th>
            <th class="text-no-wrap">
              割り当て
            </th>
            <th class="text-no-wrap">
              許可
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="period in store.periods"
            :key="period.device_period_id"
            :data-test="`period-${period.device_period_id}`"
            :class="{ 'text-disabled': period.status === 'ended' }"
          >
            <td class="text-no-wrap">
              <RouterLink
                :data-test="`period-link-${period.device_period_id}`"
                :to="{ name: 'admin-users.device-period', params: { id: period.device_period_id } }"
              >
                {{ period.account }}
              </RouterLink>
            </td>
            <td>{{ period.org_name }}</td>
            <td class="text-no-wrap">
              {{ formatJst(period.started_at) }}
            </td>
            <td class="text-no-wrap">
              <v-chip
                :color="period.status === 'active' ? 'success' : 'grey'"
                size="small"
              >
                {{ PERIOD_STATUS_LABELS[period.status] }}
              </v-chip>
              <div
                v-if="period.ended_at"
                class="text-caption"
              >
                {{ formatJst(period.ended_at) }} に終了
              </div>
            </td>
            <td
              :data-test="`period-access-${period.device_period_id}`"
              class="text-no-wrap"
            >
              {{ ACCESS_LABELS[period.access ?? 'everyone'] }}
            </td>
            <td
              :data-test="`period-assignments-${period.device_period_id}`"
              class="text-no-wrap"
            >
              {{ openAssignments(period) }} 件
              <span
                v-if="period.assignments.length > openAssignments(period)"
                class="text-caption text-medium-emphasis"
              >
                (全 {{ period.assignments.length }} 件)
              </span>
            </td>
            <td :data-test="`period-grants-${period.device_period_id}`">
              {{ activeServices(period) }}
            </td>
          </tr>
        </tbody>
      </v-table>
      <div class="text-caption text-medium-emphasis px-4 py-2">
        割り当ては今有効なもの (始まる前のものを含む) の数です。許可は今有効なサービスです。日時は日本時間 (JST) です。
      </div>
    </v-card>

    <StartDevicePeriodDialog
      v-model="startOpen"
      :loading="store.working"
      :error="startError"
      @submit="start"
    />
  </div>
</template>
