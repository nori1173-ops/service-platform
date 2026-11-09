<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { NOTE_MAX, maxRule } from '../labels'
import { pathLabel } from '../periodLabels'
import type { Group, GroupInput } from '../types'

const GROUP_NAME_MAX = 100

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    mode: 'create' | 'rename'
    /** 子を作るときの親。根を作るときは無し */
    parent?: Group | null
    initialName?: string
    initialNote?: string | null
    loading?: boolean
    error?: string | null
  }>(),
  { parent: null, initialName: '', initialNote: null, loading: false, error: null },
)

const emit = defineEmits<{ submit: [input: GroupInput] }>()

const form = ref<VForm | null>(null)
const name = ref('')
const note = ref('')

const nameRules = [(value: string) => !!value.trim() || '名前を入力してください', maxRule(GROUP_NAME_MAX)]
const noteRules = [maxRule(NOTE_MAX)]
const title = computed(() => {
  if (props.mode === 'rename') return 'グループの名前を変える'
  return props.parent ? '子のグループを作る' : '根のグループを作る'
})
const parentLabel = computed(() =>
  props.parent ? pathLabel(props.parent.path_names ?? [props.parent.name]) || props.parent.name : '',
)

watch(
  open,
  (value) => {
    if (value) {
      name.value = props.initialName
      note.value = props.initialNote ?? ''
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (props.loading) {
    return
  }
  const result = await form.value?.validate()
  if (!result?.valid || props.loading) {
    return
  }
  const input: GroupInput = { name: name.value.trim(), note: note.value.trim() || null }
  if (props.mode === 'create' && props.parent) {
    input.parent_group_id = props.parent.group_id
  }
  emit('submit', input)
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
        <v-card-title>{{ title }}</v-card-title>
        <v-card-subtitle v-if="mode === 'create' && parent">
          親: {{ parentLabel }}
        </v-card-subtitle>
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
          <v-text-field
            v-model="name"
            data-test="group-name"
            label="名前"
            :rules="nameRules"
            class="mb-3"
          />
          <v-textarea
            v-model="note"
            data-test="group-note"
            label="メモ (省略可)"
            rows="2"
            auto-grow
            :rules="noteRules"
          />
        </v-form>
        <p
          v-if="mode === 'create'"
          data-test="group-hint"
          class="text-caption text-medium-emphasis mb-0"
        >
          木は 3 段までです。段の名前は決めていません。例: 会社 > 部署 > 課、県 > 事務所 > 出張所、コンサル > 支店。同じ親の下に同じ名前は付けられません。親は後から変えられません。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="group-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="group-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading"
          @click="submit"
        >
          {{ mode === 'rename' ? '変える' : '作る' }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
