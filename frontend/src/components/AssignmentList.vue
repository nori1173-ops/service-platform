<script setup lang="ts">
import { onActivated, onMounted, ref } from 'vue'
import { extractApiError } from '@/services/apiError'
import { baseApi } from '@/services/baseApi'
import type { AssignmentGrant, AssignmentState, MyAssignment } from '@/types/devices'
import { DASH, formatJst } from '@/utils/format'

const STATE_LABELS: Record<AssignmentState, string> = { current: '今有効', upcoming: 'これから', ended: '終了' }
const STATE_COLORS: Record<AssignmentState, string> = { current: 'success', upcoming: 'info', ended: 'default' }

const items = ref<MyAssignment[]>([])
const loaded = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)

function period(item: MyAssignment): string {
  return `${formatJst(item.valid_from)} 〜 ${item.valid_to ? formatJst(item.valid_to) : '利用期間の終わりまで'}`
}

function grantLabel(grant: AssignmentGrant): string {
  if (!grant.current) {
    return `${grant.name} (開始前: ${formatJst(grant.starts_at)} から)`
  }
  return grant.ends_at ? `${grant.name} (${formatJst(grant.ends_at)} まで)` : grant.name
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    items.value = (await baseApi.getAssignments()).assignments
    loaded.value = true
  } catch (e: unknown) {
    error.value = extractApiError(e)?.message ?? '割り当ての一覧を読めませんでした。しばらくしてから読み直してください。'
  } finally {
    loading.value = false
  }
}

// keep-alive の中では最初の表示で onMounted と onActivated の両方が来るので、2 回目の表示から読み直す
let mounted = false
onMounted(() => {
  mounted = true
  void load()
})
onActivated(() => {
  if (mounted) {
    mounted = false
    return
  }
  void load()
})
</script>

<template>
  <v-card
    variant="flat"
    border
    class="mb-4"
  >
    <v-card-item>
      <v-card-title>割り当てられた通信機と許可されたサービス</v-card-title>
      <v-card-subtitle>管理者が割り当てた通信機と、その通信機で使えるサービスです。</v-card-subtitle>
    </v-card-item>
    <v-progress-linear
      v-if="loading"
      indeterminate
      color="primary"
    />
    <v-card-text>
      <v-alert
        v-if="error"
        data-test="assignments-error"
        type="error"
        variant="tonal"
        class="mb-4"
      >
        {{ error }}
        <template #append>
          <v-btn
            data-test="assignments-retry"
            variant="text"
            @click="load()"
          >
            読み直す
          </v-btn>
        </template>
      </v-alert>

      <v-alert
        v-if="loaded && !error && items.length === 0"
        data-test="assignments-empty"
        type="info"
        variant="tonal"
      >
        割り当てられた通信機はありません。
      </v-alert>

      <v-table
        v-if="items.length"
        density="comfortable"
      >
        <thead>
          <tr>
            <th>状態</th>
            <th>通信機アカウント</th>
            <th>組織</th>
            <th>有効期間</th>
            <th>許可されたサービス</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.assignment_id"
            :data-test="`assignment-${item.assignment_id}`"
            :class="{ 'text-medium-emphasis': item.state === 'ended' }"
          >
            <td>
              <v-chip
                size="small"
                :color="STATE_COLORS[item.state]"
                variant="tonal"
              >
                {{ STATE_LABELS[item.state] }}
              </v-chip>
            </td>
            <td>{{ item.account }}</td>
            <td>
              {{ item.org_name ?? DASH }}
              <div
                v-if="item.target_type === 'group'"
                class="text-caption text-medium-emphasis"
              >
                グループ: {{ item.group_label ?? DASH }}
              </div>
            </td>
            <td class="text-no-wrap">
              {{ period(item) }}
            </td>
            <td>
              <span v-if="item.grants.length === 0">{{ DASH }}</span>
              <div
                v-for="grant in item.grants"
                :key="`${grant.service}-${grant.starts_at}`"
              >
                {{ grantLabel(grant) }}
              </div>
            </td>
          </tr>
        </tbody>
      </v-table>
    </v-card-text>
  </v-card>
</template>
