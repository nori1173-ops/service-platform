<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { initialStartInput, intervalError, jstToIso, toJstInput } from '../periodLabels'
import type { AssignInput, AssignTarget, Group, UserSummary } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    startedAt: string
    users: UserSummary[]
    /** 割り当ての相手に選べるグループ (稼働中だけを出す) */
    groups?: Group[]
    account?: string
    orgName?: string
    selfUserId?: string
    loading?: boolean
    error?: string | null
  }>(),
  { groups: () => [], account: '', orgName: '', selfUserId: '', loading: false, error: null },
)

const emit = defineEmits<{ submit: [input: AssignInput] }>()

const form = ref<VForm | null>(null)
const target = ref<AssignTarget>('user')
const userId = ref<string | null>(null)
const groupId = ref<string | null>(null)
const from = ref('')
const to = ref('')
const intervalMessage = ref<string | null>(null)

// 停止中の利用者と admin 自身も割り当てられる (通信機ログインは停止で止まる。判断 9)。台帳に無い利用者は選べない
const choices = computed(() =>
  props.users
    .filter((user): user is UserSummary & { user_id: string } => user.in_ledger && !!user.user_id)
    .map((user) => {
      const notes = [
        user.org_name ? ` / ${user.org_name}` : '',
        user.status === 'suspended' ? ' (停止中)' : '',
        user.user_id === props.selfUserId ? ' (自分)' : '',
      ]
      return { id: user.user_id, label: `${user.email ?? user.user_id}${notes.join('')}` }
    }),
)

const userRules = [(value: string | null) => choices.value.some((item) => item.id === value) || '利用者を選んでください']

// グループは経路の名前 (根から) で出す。一覧は木の順に並んでいる
const groupChoices = computed(() => {
  const names = new Map(props.groups.map((group) => [group.group_id, group.name]))
  return props.groups
    .filter((group) => group.status === 'active')
    .map((group) => ({
      id: group.group_id,
      label: group.path.map((id) => names.get(id) ?? '?').join(' > '),
    }))
})
const groupRules = [
  (value: string | null) => groupChoices.value.some((item) => item.id === value) || 'グループを選んでください',
]

watch(
  open,
  (value) => {
    if (value) {
      target.value = 'user'
      userId.value = null
      groupId.value = null
      from.value = initialStartInput(props.startedAt)
      to.value = ''
      intervalMessage.value = null
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (props.loading) {
    return
  }
  intervalMessage.value = intervalError(from.value, to.value, props.startedAt)
  const result = await form.value?.validate()
  const chosen = target.value === 'user' ? userId.value : groupId.value
  if (intervalMessage.value || !result?.valid || !chosen || props.loading) {
    return
  }
  emit('submit', {
    target_type: target.value,
    ...(target.value === 'user' ? { user_id: chosen } : { group_id: chosen }),
    valid_from: jstToIso(from.value)!,
    valid_to: to.value ? jstToIso(to.value) : null,
  })
}
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="600"
    persistent
  >
    <v-card>
      <v-card-item>
        <v-card-title>割り当てる</v-card-title>
        <v-card-subtitle>{{ account }} / {{ orgName }}</v-card-subtitle>
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
        <v-form
          ref="form"
          :disabled="loading"
          @submit.prevent="submit"
        >
          <v-btn-toggle
            v-model="target"
            mandatory
            density="compact"
            variant="outlined"
            color="primary"
            class="mb-3"
          >
            <v-btn
              value="user"
              data-test="assign-target-user"
            >
              人
            </v-btn>
            <v-btn
              value="group"
              data-test="assign-target-group"
            >
              グループ
            </v-btn>
          </v-btn-toggle>
          <v-autocomplete
            v-if="target === 'group'"
            v-model="groupId"
            data-test="assign-group"
            label="グループ"
            :items="groupChoices"
            item-title="label"
            item-value="id"
            :rules="groupRules"
            no-data-text="稼働中のグループがありません"
            class="mb-3"
          />
          <v-autocomplete
            v-else
            v-model="userId"
            data-test="assign-user"
            label="利用者"
            :items="choices"
            item-title="label"
            item-value="id"
            :rules="userRules"
            no-data-text="割り当てられる利用者がいません"
            class="mb-3"
          />
          <v-text-field
            v-model="from"
            data-test="assign-from"
            label="開始 (日本時間 / JST)"
            type="datetime-local"
            step="1"
            :min="toJstInput(startedAt)"
            class="mb-3"
          />
          <v-text-field
            v-model="to"
            data-test="assign-to"
            label="終了 (日本時間 / JST、省略可)"
            type="datetime-local"
            step="1"
          />
        </v-form>
        <v-alert
          v-if="intervalMessage"
          data-test="interval-error"
          type="error"
          variant="tonal"
          density="compact"
          class="mt-3"
        >
          {{ intervalMessage }}
        </v-alert>
        <p class="text-caption text-medium-emphasis mt-3 mb-0">
          終了を省略すると利用期間の終わりまで有効です。終了の時刻は有効期間に含みません。同じ相手 (利用者かグループ) の有効期間は重ねられません。グループ宛は、そのグループと下の段のグループに属する人に効きます。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="assign-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="assign-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading"
          @click="submit"
        >
          割り当てる
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
