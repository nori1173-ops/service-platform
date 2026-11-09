<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { UserSummary } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    users: UserSummary[]
    /** もう属している利用者 (選べない) */
    memberIds?: string[]
    groupLabel?: string
    loading?: boolean
    error?: string | null
  }>(),
  { memberIds: () => [], groupLabel: '', loading: false, error: null },
)

const emit = defineEmits<{ submit: [userId: string] }>()

const userId = ref<string | null>(null)

// 停止中の利用者も属させられる (通信機の利用は停止で止まる。割り当てと同じ)
const choices = computed(() =>
  props.users
    .filter((user): user is UserSummary & { user_id: string } => user.in_ledger && !!user.user_id)
    .filter((user) => !props.memberIds.includes(user.user_id))
    .map((user) => ({
      id: user.user_id,
      label: `${user.email ?? user.user_id}${user.org_name ? ` / ${user.org_name}` : ''}${user.status === 'suspended' ? ' (停止中)' : ''}`,
    })),
)

watch(
  open,
  (value) => {
    if (value) {
      userId.value = null
    }
  },
  { immediate: true },
)

function submit(): void {
  if (!props.loading && userId.value && choices.value.some((item) => item.id === userId.value)) {
    emit('submit', userId.value)
  }
}
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="560"
    persistent
  >
    <v-card>
      <v-card-item>
        <v-card-title>グループに属させる</v-card-title>
        <v-card-subtitle>{{ groupLabel }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          v-if="error"
          data-test="dialog-error"
          type="error"
          variant="tonal"
          class="mb-4"
        >
          {{ error }}
        </v-alert>
        <v-autocomplete
          v-model="userId"
          data-test="member-user"
          label="利用者"
          :items="choices"
          item-title="label"
          item-value="id"
          :disabled="loading"
          no-data-text="属させられる利用者がいません"
        />
        <p class="text-caption text-medium-emphasis mb-0">
          このグループと、その上の段のグループ宛の割り当てが、この利用者に効きます。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="member-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="member-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading || !userId"
          @click="submit"
        >
          属させる
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
