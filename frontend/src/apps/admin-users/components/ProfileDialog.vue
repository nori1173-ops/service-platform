<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { CONTACT_MAX, NOTE_MAX, ORG_NAME_MAX, maxRule } from '../labels'
import type { ProfileInput, UserKind } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    kind: UserKind | null
    orgName: string | null
    contact: string | null
    note: string | null
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{ submit: [input: ProfileInput] }>()

const form = ref<VForm | null>(null)
const orgNameInput = ref('')
const contactInput = ref('')
const noteInput = ref('')

const orgRules = [
  (value: string) => props.kind !== 'customer' || !!value.trim() || 'お客様の組織名は消せません',
  maxRule(ORG_NAME_MAX),
]

function reset(): void {
  orgNameInput.value = props.orgName ?? ''
  contactInput.value = props.contact ?? ''
  noteInput.value = props.note ?? ''
}

watch(open, (value) => value && reset(), { immediate: true })

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (result?.valid) {
    emit('submit', {
      org_name: orgNameInput.value.trim(),
      contact: contactInput.value.trim(),
      note: noteInput.value.trim(),
    })
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
        <v-card-title>台帳の項目を直す</v-card-title>
      </v-card-item>
      <v-card-text>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <v-text-field
            v-model="orgNameInput"
            data-test="profile-org"
            label="組織名"
            :rules="orgRules"
            class="mb-3"
          />
          <v-text-field
            v-model="contactInput"
            data-test="profile-contact"
            label="連絡先 (別経路の本人確認に使う)"
            :rules="[maxRule(CONTACT_MAX)]"
            class="mb-3"
          />
          <v-textarea
            v-model="noteInput"
            data-test="profile-note"
            label="メモ"
            rows="2"
            auto-grow
            :rules="[maxRule(NOTE_MAX)]"
          />
        </v-form>
        <p class="text-caption text-medium-emphasis mt-2 mb-0">
          空にした項目は台帳から消えます。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="profile-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="profile-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          保存する
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
